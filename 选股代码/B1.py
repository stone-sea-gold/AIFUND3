"""
通达信量价共振选股策略 (B1)
输入: DataFrame 包含 ['date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'circulation_market_cap']
      (与 fetch_kline 返回的字段命名一致，circulation_market_cap 需由调用方从外部数据源提供)
输出: 带有 'XG_Signal' (选股信号) 的 DataFrame
"""
import pandas as pd
import numpy as np


def calculate_tongdaxin_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    将通达信量价共振选股公式转化为 Pandas 计算逻辑
    """
    df = df.copy()
    df = df.sort_values(by='date').reset_index(drop=True)

    # ================ 1. 寻找超卖机会 (KDJ的J值条件) ================
    den = df['high'].rolling(window=9).max() - df['low'].rolling(window=9).min()
    llv_l_9 = df['low'].rolling(window=9).min()
    rsv = np.where(den == 0, 50, (df['close'] - llv_l_9) / den * 100)
    rsv_series = pd.Series(rsv, index=df.index)

    df['K'] = rsv_series.ewm(alpha=1/3, adjust=False).mean()
    df['D'] = df['K'].ewm(alpha=1/3, adjust=False).mean()
    df['J'] = 3 * df['K'] - 2 * df['D']
    j_ok = df['J'] <= 13

    # ================ 2. 资金吸筹判定 ================
    real_yang = (df['close'] > df['open']) & ~(df['close'] < df['close'].shift(1))
    real_yin = (df['close'] < df['open']) & ~(df['close'] > df['close'].shift(1))

    vol_yang = df['volume'] * real_yang
    vol_yin = df['volume'] * real_yin

    vol_yang21 = vol_yang.rolling(window=21).sum()
    vol_yin21 = vol_yin.rolling(window=21).sum()
    vol_yang14 = vol_yang.rolling(window=14).sum()
    vol_yin14 = vol_yin.rolling(window=14).sum()

    yangyin_ok = (vol_yang21 > 1.5 * vol_yin21) | (vol_yang14 > 1.5 * vol_yin14)

    # ================ 3. 基础标的过滤 ================
    a28 = df['amount'].rolling(window=28).mean() / 100_000_000
    lq = a28 >= 0.005

    mv = df['circulation_market_cap'] / 100_000_000
    mvok = mv >= 50

    # ================ 4. 严格防雷 ================
    llv_o_28 = df['open'].rolling(window=28).min()
    hhv_o_28 = df['open'].rolling(window=28).max()
    o85 = llv_o_28 + 0.925 * (hhv_o_28 - llv_o_28)
    top15o = df['open'] >= o85

    fd15 = (df['close'] < df['close'].shift(1)) & (df['close'] <= df['open']) & (df['volume'] >= 1.15 * df['volume'].shift(1))
    good28 = (top15o & fd15).rolling(window=28).sum() == 0

    maxvol28 = df['volume'].rolling(window=28).max()
    max28_ok = ((df['volume'] == maxvol28) & real_yin).rolling(window=28).sum() == 0

    # ================ 5. 异动触发引擎 ================
    avg40 = df['volume'].rolling(window=40).mean()
    plry = (df['volume'] > 1.8 * df['volume'].shift(1)) & (df['close'] > df['open']) & (df['volume'] > avg40)
    plry_cnt = plry.rolling(window=28).sum() >= 3

    v40p = df['volume'].shift(1).rolling(window=40).sum() / 40
    bd = (df['close'] > df['close'].shift(1)) & (df['close'] >= df['open'])
    bigv = df['volume'] > 1.75 * v40p

    llv_c_40 = df['close'].rolling(window=40).min()
    hhv_c_40 = df['close'].rolling(window=40).max()
    r55 = llv_c_40 + 0.55 * (hhv_c_40 - llv_c_40)
    posok = df['close'] > r55

    trigger = plry_cnt | (bd & bigv & posok)

    # ================ 6. 最终综合与均线趋势共振 ================
    xg = trigger & j_ok & lq & mvok & good28 & max28_ok & yangyin_ok

    wl = df['close'].ewm(span=10, adjust=False).mean().ewm(span=10, adjust=False).mean()
    yl = (df['close'].rolling(window=14).mean() +
          df['close'].rolling(window=28).mean() +
          df['close'].rolling(window=57).mean() +
          df['close'].rolling(window=114).mean()) / 4

    b1 = xg & (wl > yl) & (df['close'] > yl)

    df['XG_Signal'] = b1
    return df
