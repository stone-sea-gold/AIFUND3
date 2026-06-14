"""
盯盘策略协议模板
================
用户新建盯盘策略时，复制此文件并修改其中的函数实现。

系统通过以下字段识别和加载盯盘策略：
- STRATEGY_NAME: 策略显示名称
- STRATEGY_DESC: 策略描述
- NEED_MINUTE_KLINE: 是否需要分钟K线数据（bool）
- MINUTE_PERIOD: 分钟K线周期，1 或 5（NEED_MINUTE_KLINE=True 时有效）
- SIGNALS: 信号列表，每个信号是一个 dict
"""

# ========== 元信息 ==========
STRATEGY_NAME = "示例盯盘策略"
STRATEGY_DESC = "策略描述，说明此策略的用途和适用场景"

# ========== 数据需求 ==========
NEED_MINUTE_KLINE = False   # True = 需要拉取分钟K线，False = 仅需实时报价
MINUTE_PERIOD = 1           # 1 = 1分钟K线，5 = 5分钟K线

# ========== 信号定义 ==========
SIGNALS = [
    {
        "name": "信号名称",           # 如 "放量突破"、"缩量回踩均线"
        "desc": "信号描述",           # 说明触发条件
        "level": "强烈",              # 报警等级标签（用户自定义，如 强烈/温和/关注）
        "evaluate": None,             # callable(quote, minute_bars) -> bool
        "invalidation": None,         # callable(quote, minute_bars) -> bool
    },
]

"""
========== evaluate 函数签名 ==========
def evaluate(quote: dict, minute_bars: list[dict] | None) -> bool
    参数:
        quote -- get_quotes_batch 返回的单只股票报价字典
            字段: code, price, last_close, open, high, low, volume, amount, pct_chg, servertime
        minute_bars -- get_minute_data 返回的当日分钟K线列表（如果 NEED_MINUTE_KLINE=False 则为 None）
            每根K线字段: open, close, high, low, vol, amount, year, month, day, hour, minute
    返回:
        True = 触发信号，False = 未触发

========== invalidation 函数签名 ==========
def invalidation(quote: dict, minute_bars: list[dict] | None) -> bool
    参数: 同 evaluate
    返回:
        True = 信号失效（走坏删除），False = 信号仍然有效
"""
