"""
全局配置面板 — 统一管理各模块默认参数

用法:
    from server.settings import get_settings
    s = get_settings()
    s.get("scan", "top_n")      # 获取
    s.set("scan", "top_n", 20)  # 设置（自动持久化）
    s.get_section("scan")       # 获取整个模块的配置
"""

import json
import threading
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent / "data"
_SETTINGS_FILE = _DATA_DIR / "settings.json"

# ── 默认配置（首次运行时写入 settings.json）──────────────────────

DEFAULTS = {
    "scan": {
        "strategy": "b1",
        "pool": "沪深300",
        "period": "day",
        "count": 150,
        "delay": 0.15,
        "workers": 4,
        "top_n": 30,
        "min_score": 25,
        "exclude_st": True,
        "min_listing_days": 120,
        "exclude_death_cross": True,
        "exclude_below_yellow": True,
        "use_tdx_data": True,
        "tdx_stale_days": 2,
    },
    "backtest": {
        "strategy": "b1",
        "pool": "沪深300",
        "top_n": 10,
        "min_score": 25,
        "holding_days": 3,
        "initial_capital": 100000,
        "stop_loss_pct": 3.0,
        "gap_up_pct": 4.0,
    },
    "monitor": {
        "strategies": [],
        "max_targets": 100,
    },
}

# ── 参数元数据：用于前端渲染（标签、类型、范围）─────────────────

PARAM_META = {
    "scan": {
        "strategy":          {"label": "选股策略",      "type": "select", "source": "strategies"},
        "pool":              {"label": "股票池",        "type": "select", "source": "pools"},
        "period":            {"label": "K线周期",       "type": "select", "options": ["day", "week", "month"]},
        "count":             {"label": "K线根数",       "type": "int",    "min": 50,  "max": 500},
        "delay":             {"label": "请求间隔(秒)",  "type": "float",  "min": 0.05, "max": 5.0, "step": 0.05},
        "workers":           {"label": "并发线程数",     "type": "int",    "min": 1,   "max": 16},
        "top_n":             {"label": "输出前N只",     "type": "int",    "min": 1,   "max": 100},
        "min_score":         {"label": "最低入围分数",   "type": "int",    "min": 0,   "max": 100},
        "exclude_st":        {"label": "排除ST股",      "type": "bool"},
        "min_listing_days":  {"label": "最短上市天数",   "type": "int",    "min": 0,   "max": 500},
        "exclude_death_cross":    {"label": "排除死叉", "type": "bool"},
        "exclude_below_yellow":   {"label": "排除跌破黄线", "type": "bool"},
        "use_tdx_data":      {"label": "使用通达信数据", "type": "bool"},
        "tdx_stale_days":    {"label": "数据滞后天数",  "type": "int",    "min": 0,   "max": 10},
    },
    "backtest": {
        "strategy":        {"label": "回测策略",       "type": "select", "source": "strategies"},
        "pool":            {"label": "股票池",         "type": "select", "source": "pools"},
        "top_n":           {"label": "每轮选股数",     "type": "int",    "min": 1,   "max": 100},
        "min_score":       {"label": "最低入围分数",   "type": "int",    "min": 0,   "max": 100},
        "holding_days":    {"label": "持仓天数",       "type": "int",    "min": 1,   "max": 30},
        "initial_capital": {"label": "初始资金",       "type": "float",  "min": 1000, "max": 10000000, "step": 1000},
        "stop_loss_pct":   {"label": "止损比例(%)",   "type": "float",  "min": 0.5, "max": 20.0, "step": 0.5},
        "gap_up_pct":      {"label": "高开取消止损(%)", "type": "float", "min": 0.5, "max": 20.0, "step": 0.5},
    },
    "monitor": {
        "strategies":    {"label": "盯盘策略",     "type": "multi_select", "source": "monitor_strategies"},
        "max_targets":   {"label": "目标池上限",   "type": "int",          "min": 1, "max": 500},
    },
}

# ── 分组元数据 ──────────────────────────────────────────────────

SECTIONS = [
    {"id": "scan",     "label": "选股扫描", "icon": "&#9670;", "desc": "选股扫描任务的默认参数"},
    {"id": "backtest", "label": "回测系统", "icon": "&#9654;", "desc": "回测任务和止损策略的默认参数"},
    {"id": "monitor",  "label": "盯盘助手", "icon": "&#9673;", "desc": "盯盘策略和目标池的默认设置"},
]


class Settings:
    """全局配置单例（线程安全）"""

    def __init__(self, path: Path | None = None):
        self._path = path or _SETTINGS_FILE
        self._lock = threading.Lock()
        self._data = self._load()

    # ── 读取 ──────────────────────────────────────────────────

    def get(self, section: str, key: str):
        """获取某个配置项"""
        with self._lock:
            return self._data.get(section, {}).get(key)

    def get_section(self, section: str) -> dict:
        """获取某个模块的全部配置"""
        with self._lock:
            return dict(self._data.get(section, {}))

    def get_all(self) -> dict:
        """获取全部配置（深拷贝）"""
        with self._lock:
            return json.loads(json.dumps(self._data))

    # ── 写入 ──────────────────────────────────────────────────

    def set(self, section: str, key: str, value) -> None:
        """设置单个配置项并持久化"""
        with self._lock:
            if section not in self._data:
                self._data[section] = {}
            self._data[section][key] = value
            self._save()

    def update_section(self, section: str, updates: dict) -> None:
        """批量更新某个模块的配置并持久化"""
        with self._lock:
            if section not in self._data:
                self._data[section] = {}
            self._data[section].update(updates)
            self._save()

    def reset_section(self, section: str) -> None:
        """重置某个模块为默认值"""
        with self._lock:
            if section in DEFAULTS:
                self._data[section] = json.loads(json.dumps(DEFAULTS[section]))
                self._save()

    def reset_all(self) -> None:
        """重置全部为默认值"""
        with self._lock:
            self._data = json.loads(json.dumps(DEFAULTS))
            self._save()

    # ── 元数据（给前端用）──────────────────────────────────────

    def get_meta(self) -> dict:
        """返回参数元数据和分组信息"""
        return {
            "sections": SECTIONS,
            "params": PARAM_META,
        }

    # ── 文件读写 ──────────────────────────────────────────────

    def _load(self) -> dict:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                # 合并：已有字段保留，新增字段用默认值补全
                merged = json.loads(json.dumps(DEFAULTS))
                for section, params in data.items():
                    if section in merged and isinstance(params, dict):
                        merged[section].update(params)
                    else:
                        merged[section] = params
                return merged
            except (json.JSONDecodeError, OSError):
                pass
        # 首次运行：写入默认值
        self._save_direct(DEFAULTS)
        return json.loads(json.dumps(DEFAULTS))

    def _save(self) -> None:
        self._save_direct(self._data)

    def _save_direct(self, data: dict) -> None:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)


# ── 全局单例 ──────────────────────────────────────────────────

_instance = None


def get_settings() -> Settings:
    global _instance
    if _instance is None:
        _instance = Settings()
    return _instance
