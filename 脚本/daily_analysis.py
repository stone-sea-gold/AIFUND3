#!/usr/bin/env python3
"""
每日自动分析 — Jetson AGX
15:35 收盘后运行，调 DeepSeek 分析持仓票，结果写入日志并 git push
"""
import sys, os, argparse
from datetime import date
from pathlib import Path
import requests

PROJECT_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR  = PROJECT_ROOT / "脚本"
LOG_DIR      = PROJECT_ROOT / "日志"
ENV_FILE     = PROJECT_ROOT / ".env"

sys.path.insert(0, str(SCRIPTS_DIR))
from fetch_wave_data import analyze, calc_ema, calc_sma

DEFAULT_STOCKS = {
    "002452": "长高电新",
    "600744": "华银电力",
    "600742": "富维股份",
    "002202": "金风科技",
    "002498": "汉缆股份",
    "000422": "湖北宜化",
}

def load_env():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

def extract_indicators(data: dict, name: str) -> dict:
    klines = data["klines"]
    latest = data["latest"]
    closes = [k["close"] for k in klines]
    vols   = [k["volume"] for k in klines]

    ema10  = calc_ema(closes, 10)
    sma20  = calc_sma(closes, 20)
    white  = ema10[-1]
    yellow = sma20[-1]
    price  = latest["close"]

    if price > white and white > yellow:
        pos = "价格>白线>黄线(强势)"
    elif white > price > yellow:
        pos = "白线>价格>黄线(夹心)"
    else:
        pos = "价格跌破黄线(危险)"

    hist = latest["macd_hist"]
    prev_klines = klines[-2] if len(klines) >= 2 else klines[-1]
    prev_closes = [k["close"] for k in klines[:-1]]
    prev_ema10  = calc_ema(prev_closes, 10) if prev_closes else ema10
    signal = "金叉" if hist > 0 else "死叉"

    avg5v   = sum(vols[-6:-1]) / 5 if len(vols) >= 6 else vols[-1]
    vol_r   = round(vols[-1] / avg5v, 2) if avg5v else 1.0
    chg1    = round((price - klines[-2]["close"]) / klines[-2]["close"] * 100, 2) if len(klines) >= 2 else 0

    highs9  = [k["high"] for k in klines[-9:]]
    lows9   = [k["low"]  for k in klines[-9:]]
    h9, l9  = max(highs9), min(lows9)
    rsv     = round((price - l9) / (h9 - l9) * 100, 1) if h9 != l9 else 50

    return {
        "code":     data["code"],
        "name":     name,
        "date":     latest["date"],
        "price":    price,
        "change":   chg1,
        "signal":   signal,
        "hist":     round(hist, 4),
        "dif":      round(latest["macd_dif"], 4),
        "dea":      round(latest["macd_dea"], 4),
        "white":    round(white, 2),
        "yellow":   round(yellow, 2),
        "position": pos,
        "kdj_rsv":  rsv,
        "vol_ratio": vol_r,
        "vol_trend": latest.get("vol_trend", ""),
    }

SYSTEM_PROMPT = """你是严格按两套交易系统分析A股的助手，禁止废话。

【无情操盘手】白线EMA10/黄线SMA20/KDJ/量价
- 金叉红柱：做多方向
- 死叉绿柱：防守或空仓
- 价格跌破黄线：清仓
- B1(RSV<30超卖) B2(中阳确认) B3(缩量回踩) 是买点
- S1断头铡 S2次高点反杀 S3高位缩量 是卖点

【波浪理论】MACD(5,34,5)
- 红柱扩张=3浪或5浪推进
- 红柱收缩=注意变盘
- 绿柱后金叉=新驱动浪起点

输出格式（严格）：
【股票名 代码】
信号：xxx 均线：xxx
量价：xxx
浪位：xxx
建议：持有/加仓/减仓/观望/止损
止损：xx元
结论：（10字内）"""

def call_deepseek(indicators_list: list) -> str:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY 未设置")

    stocks_text = ""
    for ind in indicators_list:
        vol_desc = "放量" if ind["vol_ratio"] > 1.5 else "缩量" if ind["vol_ratio"] < 0.7 else "温和"
        stocks_text += (
            f"\n{ind['name']}（{ind['code']}）今收{ind['price']}({'+' if ind['change']>=0 else ''}{ind['change']}%)\n"
            f"  MACD: {ind['signal']} 柱={ind['hist']} DIF={ind['dif']} DEA={ind['dea']}\n"
            f"  均线: 白{ind['white']} 黄{ind['yellow']} → {ind['position']}\n"
            f"  KDJ RSV: {ind['kdj_rsv']}  量比: {ind['vol_ratio']}({vol_desc})\n"
        )

    resp = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"今日（{date.today()}）收盘，请分析：\n{stocks_text}"},
            ],
            "temperature": 0.3,
            "max_tokens": 2000,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

def write_log(analysis: str, indicators_list: list) -> Path:
    today = date.today().strftime("%Y-%m-%d")
    log_path = LOG_DIR / f"{today}.md"

    summary = " | ".join(
        f"{i['name']} {'+' if i['change']>=0 else ''}{i['change']}% {i['signal']}"
        for i in indicators_list
    )
    existing = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    block = f"""
## DeepSeek 每日分析（自动）

> {summary}

{analysis}

---
*Jetson AGX 自动生成 · DeepSeek V3*
"""
    if existing:
        log_path.write_text(existing.rstrip() + "\n" + block, encoding="utf-8")
    else:
        log_path.write_text(f"# {today}\n" + block, encoding="utf-8")
    return log_path

def git_push(log_path: Path):
    import subprocess
    os.chdir(PROJECT_ROOT)
    subprocess.run(["git", "add", str(log_path)], check=True)
    subprocess.run(["git", "commit", "-m", f"auto: daily analysis {log_path.stem}"], check=True)
    subprocess.run(["git", "-c",
        "core.sshCommand=ssh -i /root/.ssh/id_ed25519_github_copilot -F /dev/null -o StrictHostKeyChecking=no",
        "push"], check=True)
    print("✓ git push 完成")

def main():
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--stocks", nargs="+", default=list(DEFAULT_STOCKS.keys()))
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()

    stocks = {code: DEFAULT_STOCKS.get(code, code) for code in args.stocks}
    print(f"=== 每日分析 {date.today()} ===")

    indicators_list = []
    for code, name in stocks.items():
        print(f"  拉取 {name}({code})...")
        try:
            data = analyze(code, count=60)
            ind = extract_indicators(data, name)
            indicators_list.append(ind)
            print(f"    {ind['signal']} {ind['price']} 量比{ind['vol_ratio']}")
        except Exception as e:
            print(f"    ✗ {e}")

    if not indicators_list:
        print("✗ 无数据")
        return

    print("\n调用 DeepSeek...")
    analysis = call_deepseek(indicators_list)
    print(analysis)

    log_path = write_log(analysis, indicators_list)
    print(f"\n✓ 写入 {log_path.name}")

    if not args.no_push:
        git_push(log_path)

if __name__ == "__main__":
    main()
