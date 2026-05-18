"""
选股模块 — 可调参数集中管理
"""

# 选股策略（对应 选股/strategies/ 下的文件名，不含 .py）
STRATEGY = "b1"

# 扫描范围
#   "全A"      — 全部A股（~5000只，耗时较长）
#   "沪深300"   — 沪深300成分股
#   "中证500"   — 中证500成分股
#   "自选"      — 观察仓/watchlist.txt
STOCK_POOL = "沪深300"

# K线参数
SCAN_PERIOD = "day"          # 日K / 周K / 月K
SCAN_COUNT = 150             # 每只取K线根数（不含内部预热）
REQUEST_DELAY = 0.15         # 每只请求间隔(秒)，防封IP
PARALLEL_WORKERS = 4         # 并发抓取线程数

# 过滤垫底条件（满足任一直接排除）
# ──────────────────────────────
# ST股（去ST）
EXCLUDE_ST = True
# 上市不足N天（日均线未成型）
MIN_LISTING_DAYS = 120
# 停牌/一字板（无成交量）
MIN_VOLUME_RATIO = 0.1       # 近20日最低日均量 / 近20日均量
# 死叉中（白线下穿黄线）
EXCLUDE_DEATH_CROSS = True
# 价格跌破黄线
EXCLUDE_BELOW_YELLOW = True

# 输出控制
TOP_N = 30                   # 输出前N只
MIN_SCORE = 25               # 最低入围总分
# 通达信本地数据源（K线读取加速）
TDX_DATA_DIR = "D:/BaiduNetdiskDownload"     # 通达信安装目录（vipdoc 的父级，TDX直接更新此目录）
TDX_STALE_DAYS = 2            # 本地数据滞后超过N个交易日时，用API增量补充
# 设为 False 可完全回退到东方财富 API
USE_TDX_DATA = True

OUTPUT_DIR = "选股/选股结果"  # 报告输出目录（相对项目根）
SAVE_CACHE = True            # K线数据源优先使用通达信本地文件（由 kline_source.py 处理）
