#!/usr/bin/env python3
"""
截图 MACD 数据提取与对比工具

用法:
    python3 extract_screenshot.py                      # 处理今日截图目录
    python3 extract_screenshot.py 截图/2026-04-10      # 指定目录
    python3 extract_screenshot.py 截图/2026-04-10/长高电新.png  # 单张图片

工作流:
    1. 扫描截图目录，按文件名或 OCR 识别股票代码
    2. 多策略预处理 + Tesseract OCR 提取 MACD 数值
    3. 调用 fetch_wave_data 获取计算值
    4. 输出对比报告

截图命名建议: 包含股票代码或名称，如 002452.png / 长高电新.png
"""

import sys
import os
import re
import glob
import datetime

import pytesseract
import cv2
import numpy as np
from PIL import Image

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VAULT_DIR = os.path.dirname(SCRIPT_DIR)

# 持仓股票映射（与仓位总览一致）
HOLDINGS = {
    "002452": "长高电新",
    "600744": "华银电力",
    "600742": "富维股份",
    "002202": "金风科技",
    "002498": "汉缆股份",
    "000422": "湖北宜化",
}

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


# ═══════════════════════════════════════════════════════════════
# OCR 预处理与提取
# ═══════════════════════════════════════════════════════════════

def ocr_multi_strategy(img_path: str) -> str:
    """
    多策略预处理 + OCR，合并结果提高召回率。
    行情软件通常是深色背景 + 彩色文字，需要专门处理。
    """
    img = cv2.imread(img_path)
    if img is None:
        return ""

    texts = []

    # 策略1: 灰度反色 + 自适应阈值（适配深色背景白色/黄色文字）
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    inv = cv2.bitwise_not(gray)
    _, bin1 = cv2.threshold(inv, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    texts.append(pytesseract.image_to_string(bin1, lang="chi_sim+eng"))

    # 策略2: 提取高亮度像素（白/黄/亮红/亮绿 文字）
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    bright_mask = np.zeros(gray.shape, dtype=np.uint8)
    bright_mask[hsv[:, :, 2] > 140] = 255
    texts.append(pytesseract.image_to_string(bright_mask, lang="chi_sim+eng"))

    # 策略3: 分别提取红色和绿色通道（MACD 红绿柱对应的文字颜色）
    b, g, r = cv2.split(img)
    # 红色文字: R 高, G/B 低
    red_mask = np.zeros(gray.shape, dtype=np.uint8)
    red_mask[(r > 150) & (g < 120) & (b < 120)] = 255
    if red_mask.any():
        texts.append(pytesseract.image_to_string(red_mask, lang="eng"))
    # 绿色文字: G 高, R/B 低
    green_mask = np.zeros(gray.shape, dtype=np.uint8)
    green_mask[(g > 150) & (r < 120) & (b < 120)] = 255
    if green_mask.any():
        texts.append(pytesseract.image_to_string(green_mask, lang="eng"))

    # 策略4: CLAHE 增强后直接 OCR
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    texts.append(pytesseract.image_to_string(enhanced, lang="chi_sim+eng"))

    return "\n".join(texts)


def identify_stock(filename: str, ocr_text: str):
    """根据文件名或 OCR 文本识别股票"""
    basename = os.path.splitext(os.path.basename(filename))[0]

    # 优先匹配文件名
    for code, name in HOLDINGS.items():
        if code in basename or name in basename:
            return code, name

    # OCR 文本匹配
    for code, name in HOLDINGS.items():
        if code in ocr_text or name in ocr_text:
            return code, name

    # 兜底: 任意 6 位 A 股代码
    codes = re.findall(r"[0368]\d{5}", ocr_text)
    if codes:
        c = codes[0]
        return c, HOLDINGS.get(c, c)

    return None, None


def extract_macd_values(text: str) -> dict:
    """
    从 OCR 文本中提取 MACD 相关数值。
    兼容通达信、同花顺、东方财富等常见格式。
    """
    vals = {}

    # DIF / DIFF
    for pat in [
        r"DIF[F]?[:\s=]*(-?\d+\.?\d*)",
        r"DIF[F]?\s*[:=]?\s*(-?\d+\.?\d*)",
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                vals["dif"] = float(m.group(1))
            except ValueError:
                pass
            break

    # DEA
    for pat in [
        r"DEA[:\s=]*(-?\d+\.?\d*)",
        r"DEA\s*[:=]?\s*(-?\d+\.?\d*)",
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                vals["dea"] = float(m.group(1))
            except ValueError:
                pass
            break

    # MACD 柱状图值（注意区分 "MACD(12,26,9)" 参数行和数值行）
    for pat in [
        r"MACD[:\s=]*(-?\d+\.\d+)",            # MACD: 0.123
        r"MACD\s*[:=]\s*(-?\d+\.?\d*)",
        r"柱[:\s]*(-?\d+\.?\d*)",
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                vals["hist"] = float(m.group(1))
            except ValueError:
                pass
            break

    return vals


# ═══════════════════════════════════════════════════════════════
# 计算值获取（复用 fetch_wave_data）
# ═══════════════════════════════════════════════════════════════

def fetch_calc_macd(code: str) -> dict:
    """获取脚本计算的 MACD 值（标准 12,26,9）"""
    sys.path.insert(0, SCRIPT_DIR)
    from fetch_wave_data import analyze

    try:
        data = analyze(code, 60, "day")
        last_k = data["klines"][-1]
        return {
            "date": last_k["date"],
            "close": last_k["close"],
            "std": {
                "dif": last_k["macd_std_dif"],
                "dea": last_k["macd_std_dea"],
                "hist": last_k["macd_std_hist"],
            },
            "wave": {
                "dif": last_k["macd_dif"],
                "dea": last_k["macd_dea"],
                "hist": last_k["macd_hist"],
            },
        }
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

def process_one(img_path: str, calc_cache: dict):
    """处理单张截图"""
    fname = os.path.basename(img_path)
    print(f"\n{'─' * 60}")
    print(f"  {fname}")
    print(f"{'─' * 60}")

    # OCR
    print("  OCR 识别中 ...")
    ocr_text = ocr_multi_strategy(img_path)

    # 识别股票
    code, name = identify_stock(img_path, ocr_text)
    if not code:
        print(f"  [!] 未能识别股票，OCR 片段:")
        print(f"      {ocr_text[:300]}")
        return

    print(f"  股票: {name}（{code}）")

    # OCR 提取 MACD
    macd_ocr = extract_macd_values(ocr_text)
    if macd_ocr:
        print(f"  截图提取: {macd_ocr}")
    else:
        print(f"  [!] OCR 未能提取 MACD 数值（精度不足，需人工读数）")

    # 计算值
    if code not in calc_cache:
        print(f"  获取 {code} 计算数据 ...")
        calc_cache[code] = fetch_calc_macd(code)

    calc = calc_cache[code]
    if "error" in calc:
        print(f"  [!] 获取失败: {calc['error']}")
        return

    std = calc["std"]
    wave = calc["wave"]
    print(f"  日期: {calc['date']}  收盘: {calc['close']}")
    print(f"  计算 MACD(12,26,9):  DIF={std['dif']:.4f}  DEA={std['dea']:.4f}  柱={std['hist']:.4f}")
    print(f"  计算 MACD(5,34,5) :  DIF={wave['dif']:.4f}  DEA={wave['dea']:.4f}  柱={wave['hist']:.4f}")

    # 如果 OCR 提取到了数值，做对比
    if macd_ocr:
        print(f"\n  {'指标':<6} {'截图':>10} {'计算(12,26,9)':>14} {'差值':>10} {'状态':>4}")
        print(f"  {'─' * 48}")
        for key, label in [("dif", "DIF"), ("dea", "DEA"), ("hist", "MACD柱")]:
            if key in macd_ocr:
                ocr_v = macd_ocr[key]
                calc_v = std[key]
                diff = abs(ocr_v - calc_v)
                ok = "OK" if diff < 0.1 else "!!"
                print(f"  {label:<6} {ocr_v:>10.4f} {calc_v:>14.4f} {diff:>10.4f} {ok:>4}")


def collect_images(target: str) -> list:
    """收集目标路径下的所有图片"""
    if os.path.isfile(target):
        return [target]

    d = target
    if not os.path.isdir(d):
        d = os.path.join(VAULT_DIR, target)
    if not os.path.isdir(d):
        return []

    images = []
    for f in sorted(os.listdir(d)):
        if os.path.splitext(f)[1].lower() in IMAGE_EXTS:
            images.append(os.path.join(d, f))
    return images


def main():
    # 确定截图目录/文件
    if len(sys.argv) > 1:
        target = sys.argv[1]
    else:
        today = datetime.date.today().isoformat()
        target = os.path.join(VAULT_DIR, "截图", today)

    images = collect_images(target)

    if not images:
        print(f"未找到截图: {target}")
        print(f"请将行情软件截图放入截图目录，文件名建议包含股票代码或名称")
        print(f"  例: 002452.png / 长高电新.png / 长高电新_日K.png")
        sys.exit(0)

    print(f"{'═' * 60}")
    print(f"  MACD 截图提取与对比")
    print(f"  找到 {len(images)} 张截图")
    print(f"{'═' * 60}")

    calc_cache = {}  # 缓存计算值，避免重复请求

    for img_path in images:
        process_one(img_path, calc_cache)

    print(f"\n{'═' * 60}")
    print(f"  完成 — 若 OCR 提取不准，可将截图交给 Claude 直接读图对比")
    print(f"{'═' * 60}\n")


if __name__ == "__main__":
    main()
