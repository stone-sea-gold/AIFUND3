"""
通达信砖型图趋势拐点选股策略
输入: DataFrame 包含 ['date', 'open', 'close', 'high', 'low']
      (与 fetch_kline 返回的字段命名一致)
输出: 带有 'XG_Signal' (选股信号) 的 DataFrame
"""
import pandas as pd
import numpy as np


def tongdaxin_sma(series: pd.Series, n: int, m: int) -> pd.Series:
    """
    还原通达信的 SMA(X, N, M) 算法
    本质上是 alpha = M / N 的 EMA 指数加权平均
    """
    return series.ewm(alpha=m/n, adjust=False).mean()


def calculate_brick_inflection_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    将通达信"砖型图"趋势拐点选股公式转化为 Pandas 计算逻辑
    """
    df = df.copy()
    df = df.sort_values(by='date').reset_index(drop=True)

    close = df['close']
    high = df['high']
    low = df['low']

    # ================ 1. 核心动量指标构建 ================
    hhv_h_4 = high.rolling(window=4).max()
    llv_l_4 = low.rolling(window=4).min()
    den_4 = hhv_h_4 - llv_l_4

    var1a = np.where(den_4 == 0, 0, (hhv_h_4 - close) / den_4 * 100) - 90
    var1a_series = pd.Series(var1a, index=df.index)

    var2a = tongdaxin_sma(var1a_series, 4, 1) + 100

    var3a = np.where(den_4 == 0, 0, (close - llv_l_4) / den_4 * 100)
    var3a_series = pd.Series(var3a, index=df.index)

    var4a = tongdaxin_sma(var3a_series, 6, 1)
    var5a = tongdaxin_sma(var4a, 6, 1) + 100

    var6a = var5a - var2a

    # ================ 2. 趋势过滤与抗噪 ================
    brick = np.where(var6a > 4, var6a - 4, 0)
    brick_series = pd.Series(brick, index=df.index)

    # ================ 3. V型拐点与反弹力度判定 ================
    aa = brick_series > brick_series.shift(1)
    cc = brick_series.shift(1) < brick_series.shift(2)

    red_bar = brick_series - brick_series.shift(1)
    prev_green_bar = brick_series.shift(2) - brick_series.shift(1)

    rebound_cond = red_bar > (prev_green_bar * 2 / 3)

    # ================ 4. 趋势护航 ================
    short_line = close.ewm(span=10, adjust=False).mean().ewm(span=10, adjust=False).mean()

    ma14 = close.rolling(window=14).mean()
    ma28 = close.rolling(window=28).mean()
    ma57 = close.rolling(window=57).mean()
    ma114 = close.rolling(window=114).mean()
    long_line = (ma14 + ma28 + ma57 + ma114) / 4

    trend_cond = (short_line > long_line) & (close > long_line)

    # ================ 5. 最终选股 ================
    xg = aa & cc & rebound_cond & trend_cond

    df['XG_Signal'] = xg
    df['short_line'] = short_line
    df['long_line'] = long_line
    df['brick_value'] = brick_series

    return df
