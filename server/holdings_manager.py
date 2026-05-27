"""
持仓管理模块

管理持仓交易的生命周期：新增 → 编辑 → 清仓 → 归档
同步维护账户净值历史。

用法:
    manager = get_holdings_manager()
    manager.add_trade(...)
    manager.close_trade(...)
"""

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent / "data"


class HoldingsManager:
    """持仓与净值管理器（线程安全）"""

    def __init__(self, data_dir: Path | None = None):
        if data_dir is None:
            data_dir = _DATA_DIR
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.file_path = self.data_dir / "holdings.json"
        self.nav_path = self.data_dir / "nav_history.json"
        self._lock = threading.Lock()

    # ── 持仓读取 ──────────────────────────────────────────────

    def get_trades(self, status_filter: str | None = None) -> list[dict]:
        """获取交易记录，可按 status 过滤"""
        data = self._load_holdings()
        trades = data.get("trades", [])
        if status_filter and status_filter != "all":
            trades = [t for t in trades if t.get("status") == status_filter]
        return trades

    def get_trade(self, trade_id: str) -> dict | None:
        """获取单条交易记录"""
        data = self._load_holdings()
        for t in data.get("trades", []):
            if t.get("id") == trade_id:
                return t
        return None

    def get_strategies(self) -> dict:
        """获取策略下拉选项"""
        data = self._load_holdings()
        return data.get("strategies", {"buy": [], "sell": []})

    # ── 持仓写入 ──────────────────────────────────────────────

    def add_trade(
        self,
        stock_name: str,
        stock_code: str,
        buy_date: str,
        cost_price: float,
        shares: int,
        buy_strategy: str,
    ) -> dict:
        """新增持仓交易"""
        trade_id = f"t_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
        trade = {
            "id": trade_id,
            "stock_name": stock_name,
            "stock_code": stock_code,
            "buy_date": buy_date,
            "cost_price": round(cost_price, 3),
            "shares": int(shares),
            "buy_strategy": buy_strategy,
            "sell_date": None,
            "sell_price": None,
            "sell_strategy": None,
            "status": "open",
            "pnl": None,
            "pnl_pct": None,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        with self._lock:
            data = self._load_holdings()
            data.setdefault("trades", []).append(trade)
            self._save_holdings(data)
        return trade

    def edit_trade(self, trade_id: str, fields: dict) -> bool:
        """编辑持仓交易（仅 open 状态可编辑）"""
        allowed = {"stock_name", "stock_code", "buy_date", "cost_price", "shares", "buy_strategy"}
        with self._lock:
            data = self._load_holdings()
            for t in data.get("trades", []):
                if t.get("id") == trade_id:
                    if t.get("status") != "open":
                        return False
                    for k, v in fields.items():
                        if k in allowed and v is not None:
                            if k == "cost_price":
                                t[k] = round(float(v), 3)
                            elif k == "shares":
                                t[k] = int(v)
                            else:
                                t[k] = v
                    self._save_holdings(data)
                    return True
        return False

    def close_trade(
        self,
        trade_id: str,
        sell_date: str,
        sell_price: float,
        sell_strategy: str,
    ) -> dict | None:
        """清仓：记录卖出信息，计算盈亏，更新净值"""
        with self._lock:
            data = self._load_holdings()
            trade = None
            for t in data.get("trades", []):
                if t.get("id") == trade_id:
                    trade = t
                    break
            if trade is None or trade.get("status") != "open":
                return None

            cost_price = trade["cost_price"]
            shares = trade["shares"]
            total_cost = cost_price * shares
            pnl = round((sell_price - cost_price) * shares, 2)
            pnl_pct = round(pnl / total_cost * 100, 2) if total_cost else 0.0

            trade["sell_date"] = sell_date
            trade["sell_price"] = round(sell_price, 3)
            trade["sell_strategy"] = sell_strategy
            trade["status"] = "closed"
            trade["pnl"] = pnl
            trade["pnl_pct"] = pnl_pct
            self._save_holdings(data)

        # 更新净值（在锁外调用，内部有自己的锁）
        self._on_trade_close(trade_id, pnl, trade["stock_name"])
        return trade

    def delete_trade(self, trade_id: str) -> bool:
        """删除交易记录"""
        with self._lock:
            data = self._load_holdings()
            trades = data.get("trades", [])
            new_trades = [t for t in trades if t.get("id") != trade_id]
            if len(new_trades) == len(trades):
                return False
            data["trades"] = new_trades
            self._save_holdings(data)
        return True

    # ── 净值管理 ──────────────────────────────────────────────

    def get_nav(self) -> dict:
        """获取净值数据"""
        return self._load_nav()

    def init_nav(self, initial_nav: float, initial_date: str) -> dict:
        """设置初始净值"""
        with self._lock:
            nav = self._load_nav()
            nav["initial_nav"] = round(initial_nav, 2)
            nav["initial_date"] = initial_date
            nav["current_nav"] = round(initial_nav, 2)
            nav["records"] = [
                {
                    "date": initial_date,
                    "nav": round(initial_nav, 2),
                    "type": "init",
                    "note": "初始净值",
                }
            ]
            self._save_nav(nav)
        return nav

    def reset_nav(self) -> dict:
        """重置净值（清空所有记录）"""
        with self._lock:
            nav = {
                "initial_nav": 0,
                "initial_date": None,
                "current_nav": 0,
                "records": [],
            }
            self._save_nav(nav)
        return nav

    def undo_nav(self) -> dict | None:
        """撤销最后一条净值记录"""
        with self._lock:
            nav = self._load_nav()
            records = nav.get("records", [])
            if len(records) <= 1:
                return None
            removed = records.pop()
            nav["current_nav"] = records[-1]["nav"]
            self._save_nav(nav)
        return nav

    def adjust_nav(self, amount: float, direction: str, date: str, note: str = "") -> dict:
        """出入金调整"""
        with self._lock:
            nav = self._load_nav()
            if not nav.get("records"):
                return nav
            current = nav["current_nav"]
            if direction == "deposit":
                new_nav = round(current + amount, 2)
            elif direction == "withdraw":
                new_nav = round(current - amount, 2)
            else:
                return nav
            nav["current_nav"] = new_nav
            nav["records"].append(
                {
                    "date": date,
                    "nav": new_nav,
                    "type": direction,
                    "amount": round(amount, 2),
                    "note": note or ("入金" if direction == "deposit" else "出金"),
                }
            )
            self._save_nav(nav)
        return nav

    def _on_trade_close(self, trade_id: str, pnl: float, stock_name: str):
        """清仓后自动更新净值"""
        with self._lock:
            nav = self._load_nav()
            if not nav.get("records"):
                return
            new_nav = round(nav["current_nav"] + pnl, 2)
            nav["current_nav"] = new_nav
            nav["records"].append(
                {
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "nav": new_nav,
                    "type": "close",
                    "trade_id": trade_id,
                    "pnl": round(pnl, 2),
                    "note": f"清仓{stock_name}",
                }
            )
            self._save_nav(nav)

    # ── 文件读写 ──────────────────────────────────────────────

    def _load_holdings(self) -> dict:
        try:
            if self.file_path.exists():
                return json.loads(self.file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
        return {"trades": [], "strategies": {"buy": [], "sell": []}}

    def _save_holdings(self, data: dict):
        tmp = self.file_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.file_path)

    def _load_nav(self) -> dict:
        try:
            if self.nav_path.exists():
                return json.loads(self.nav_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
        return {"initial_nav": 0, "initial_date": None, "current_nav": 0, "records": []}

    def _save_nav(self, data: dict):
        tmp = self.nav_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.nav_path)


# ── 全局单例 ──────────────────────────────────────────────────

_manager = None


def get_holdings_manager() -> HoldingsManager:
    global _manager
    if _manager is None:
        _manager = HoldingsManager()
    return _manager
