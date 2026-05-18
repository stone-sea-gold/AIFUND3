"""
通达信「砖型图」趋势拐点选股公式 → Python 实现

基于通达信公式逐行翻译，输入 DataFrame 需包含:
  date, open, close, high, low
（与 fetch_kline 返回的字段命名一致）

输出在原 DataFrame 上附加列:
  brick_value : 砖型图值
  short_line  : 短期线 (EMA10双重平滑)
  long_line   : 多空线 (14/28/57/114 四均线平均)
  XG_Signal   : 最终选股信号 (AA AND CC AND 反弹力度 AND 趋势条件)

通达信原码见: 选股代码/砖型图通达信格式源码.txt
"""

import pandas as pd
import numpy as np


# ═══════════════════════════════════════════════════════════════
# 通达信 SMA / EMA 底层实现
# ═══════════════════════════════════════════════════════════════

def _tdx_sma(series: pd.Series, n: int, m: int) -> pd.Series:
    """
    通达信 SMA(X, N, M) 算法
    递推公式: Y = (X*M + Y'*(N-M)) / N
    等价于 EMA with alpha = M/N
    """
    return series.ewm(alpha=m / n, adjust=False).mean()


def _tdx_ema(series: pd.Series, n: int) -> pd.Series:
    """
    通达信 EMA(X, N)
    等价于 pandas ewm(span=N, adjust=False).mean()
    """
    return series.ewm(span=n, adjust=False).mean()


# ═══════════════════════════════════════════════════════════════
# 主计算函数
# ═══════════════════════════════════════════════════════════════

def calculate_brick_inflection_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    将通达信砖型图趋势拐点选股公式逐行转化为 Pandas 计算

    参数:
        df: DataFrame，必须含 date, open, close, high, low 列

    返回:
        原 df 附加 brick_value, short_line, long_line, XG_Signal 列
    """
    df = df.copy()
    df = df.sort_values(by='date').reset_index(drop=True)

    close = df['close']
    high = df['high']
    low = df['low']

    # ── 第一步: 核心动量指标 (VAR1A → VAR6A) ──

    # 4日最高/最低
    hhv_h_4 = high.rolling(window=4).max()
    llv_l_4 = low.rolling(window=4).min()
    den_4 = hhv_h_4 - llv_l_4

    # VAR1A := (HHV(HIGH,4) - CLOSE) / (HHV(HIGH,4) - LLV(LOW,4)) * 100 - 90
    _var1a_raw = np.where(den_4 == 0, 0.0, (hhv_h_4 - close) / den_4 * 100)
    var1a = pd.Series(_var1a_raw - 90, index=df.index)

    # VAR2A := SMA(VAR1A, 4, 1) + 100
    var2a = _tdx_sma(var1a, n=4, m=1) + 100

    # VAR3A := (CLOSE - LLV(LOW,4)) / (HHV(HIGH,4) - LLV(LOW,4)) * 100
    _var3a_raw = np.where(den_4 == 0, 0.0, (close - llv_l_4) / den_4 * 100)
    var3a = pd.Series(_var3a_raw, index=df.index)

    # VAR4A := SMA(VAR3A, 6, 1)
    var4a = _tdx_sma(var3a, n=6, m=1)

    # VAR5A := SMA(VAR4A, 6, 1) + 100
    var5a = _tdx_sma(var4a, n=6, m=1) + 100

    # VAR6A := VAR5A - VAR2A
    var6a = var5a - var2a

    # ── 第二步: 砖型图 ──
    # 砖型图 := IF(VAR6A > 4, VAR6A - 4, 0)
    brick_raw = np.where(var6a > 4, var6a - 4, 0)
    brick = pd.Series(brick_raw, index=df.index)

    # ── 第三步: V型拐点 ──
    # AA := 砖型图 > REF(砖型图, 1)
    aa = brick > brick.shift(1)
    # CC := REF(砖型图, 1) < REF(砖型图, 2)
    cc = brick.shift(1) < brick.shift(2)

    # ── 第四步: 反弹力度 ──
    # 红柱长度 := 砖型图 - REF(砖型图, 1)
    red_bar = brick - brick.shift(1)
    # 前绿柱长度 := REF(砖型图, 2) - REF(砖型图, 1)
    prev_green_bar = brick.shift(2) - brick.shift(1)
    # 反弹条件: 红柱长度 > 前绿柱长度 * 2/3
    rebound_cond = red_bar > (prev_green_bar * 2 / 3)

    # ── 第五步: 趋势线 ──
    # 短期线 := EMA(EMA(CLOSE, 10), 10)
    short_line = _tdx_ema(_tdx_ema(close, 10), 10)

    # 多空线 := (MA(CLOSE,14) + MA(CLOSE,28) + MA(CLOSE,57) + MA(CLOSE,114)) / 4
    long_line = (
        close.rolling(window=14).mean() +
        close.rolling(window=28).mean() +
        close.rolling(window=57).mean() +
        close.rolling(window=114).mean()
    ) / 4

    # 趋势条件 := 短期线 > 多空线 AND CLOSE > 多空线
    trend_cond = (short_line > long_line) & (close > long_line)

    # ── 第六步: 最终选股 ──
    # XG: AA AND CC AND 红柱长度 > 前绿柱长度*2/3 AND 趋势条件
    xg_signal = aa & cc & rebound_cond & trend_cond

    # ── 写入输出列 ──
    df['brick_value'] = brick
    df['short_line'] = short_line
    df['long_line'] = long_line
    df['XG_Signal'] = xg_signal

    return df


# ═══════════════════════════════════════════════════════════════
# 测试入口
# ═══════════════════════════════════════════════════════════════

def fetch_and_test_brick(symbol: str = "600519"):
    """从东方财富拉取日K，计算砖型图指标并打印信号摘要"""
    import sys
    from pathlib import Path

    _project = Path(__file__).resolve().parent.parent
    _scripts = _project / "脚本"
    sys.path.insert(0, str(_scripts))
    from fetch_wave_data import fetch_kline

    print(f"正在从东方财富拉取 {symbol} 的历史日K...")
    name, klines = fetch_kline(symbol, count=500, period="day")

    df = pd.DataFrame(klines)
    print("正在计算砖型图趋势拐点指标...")
    result = calculate_brick_inflection_signals(df)

    signals = result[result['XG_Signal']].copy()
    if not signals.empty and 'date' in signals.columns:
        signals['date'] = pd.to_datetime(signals['date']).dt.strftime('%Y-%m-%d')

    print(f"\n计算完成。历史共触发选股信号 {len(signals)} 次。")
    if not signals.empty:
        print("最近 5 次信号:")
        cols = ['date', 'close', 'brick_value', 'short_line', 'long_line']
        print(signals[cols].tail(5).to_string(index=False))


if __name__ == "__main__":
    fetch_and_test_brick("600519")
