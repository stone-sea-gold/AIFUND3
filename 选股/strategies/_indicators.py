"""
共享选股指标函数 — 可被任意策略模块复用

所有函数遵循策略协议签名:
  func(ind: dict, klines: list[dict], weight: int, params: dict) -> (score: int, detail: dict)
"""

import pandas as pd


def check_waterfall_divergence(ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """
    瀑布线向上发散：MA30 > MA60 > MA120 > MA240

    瀑布线由四条简单移动平均线组成:
      MA30  = MA(CLOSE, 30)
      MA60  = MA(CLOSE, 60)
      MA120 = MA(CLOSE, 120)
      MA240 = MA(CLOSE, 240)

    向下发散 = 短期均线依次在长期均线上方，表示趋势全面看多。

    参数:
      params.get("ma_periods", [30, 60, 120, 240])  — 可自定义均线周期
    """
    periods = params.get("ma_periods", [30, 60, 120, 240])
    min_bars = max(periods) + 1

    closes = [k["close"] for k in klines]
    if len(closes) < min_bars:
        return 0, {"reason": f"数据不足(需>= {min_bars} 根K线)"}

    s = pd.Series(closes)
    mas = [float(s.rolling(window=p).mean().iloc[-1]) for p in periods]

    # 检查严格递减: ma[0] > ma[1] > ma[2] > ma[3]
    diverged = all(mas[i] > mas[i + 1] for i in range(len(mas) - 1))

    labels = [f"MA{p}" for p in periods]
    values_str = " > ".join(f"{l}{v:.2f}" for l, v in zip(labels, mas))

    if diverged:
        return weight, {"mas": [round(v, 2) for v in mas],
                        "reason": f"瀑布线向上发散 {values_str}"}

    return 0, {"mas": [round(v, 2) for v in mas],
               "reason": f"瀑布线未发散 {values_str}"}
