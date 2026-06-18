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
        dividend: float = 0.0,
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
            trade["dividend"] = round(dividend, 2)

            # 同步写入分红记录
            if dividend > 0:
                self._sync_dividend_to_list(data, trade["stock_name"], trade["stock_code"], dividend, sell_date, trade_id)

            self._save_holdings(data)

        # 更新净值（在锁外调用，内部有自己的锁）
        self._on_trade_close(trade_id, pnl, trade["stock_name"], dividend, sell_date)
        return trade

    def partial_close(
        self,
        trade_id: str,
        sell_date: str,
        sell_price: float,
        sell_strategy: str,
        reduce_shares: int,
        dividend: float = 0.0,
    ) -> dict | None:
        """减仓：减少原记录股数，新建 partial 记录，更新净值"""
        with self._lock:
            data = self._load_holdings()
            trade = None
            for t in data.get("trades", []):
                if t.get("id") == trade_id:
                    trade = t
                    break
            if trade is None or trade.get("status") != "open":
                return None
            if reduce_shares <= 0 or reduce_shares >= trade["shares"]:
                return None

            cost_price = trade["cost_price"]
            total_cost = cost_price * reduce_shares
            pnl = round((sell_price - cost_price) * reduce_shares, 2)
            pnl_pct = round(pnl / total_cost * 100, 2) if total_cost else 0.0

            # 原记录减少股数
            trade["shares"] -= reduce_shares

            # 新建 partial 记录
            partial_id = f"p_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
            partial = {
                "id": partial_id,
                "parent_id": trade_id,
                "stock_name": trade["stock_name"],
                "stock_code": trade["stock_code"],
                "buy_date": trade["buy_date"],
                "cost_price": cost_price,
                "shares": reduce_shares,
                "buy_strategy": trade["buy_strategy"],
                "sell_date": sell_date,
                "sell_price": round(sell_price, 3),
                "sell_strategy": sell_strategy,
                "status": "partial",
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "dividend": round(dividend, 2),
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            data.setdefault("trades", []).append(partial)

            # 同步写入分红记录
            if dividend > 0:
                self._sync_dividend_to_list(data, trade["stock_name"], trade["stock_code"], dividend, sell_date, partial_id)

            self._save_holdings(data)

        # 更新净值
        self._on_trade_close(trade_id, pnl, trade["stock_name"], dividend, sell_date)
        return partial

    # ── 分红管理 ──────────────────────────────────────────────

    def get_dividends(self) -> list[dict]:
        """获取所有分红记录"""
        data = self._load_holdings()
        return data.get("dividends", [])

    def add_dividend(
        self,
        stock_name: str,
        stock_code: str,
        amount: float,
        date: str,
    ) -> dict:
        """手动添加分红记录"""
        div_id = f"d_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
        dividend = {
            "id": div_id,
            "stock_name": stock_name,
            "stock_code": stock_code,
            "amount": round(amount, 2),
            "date": date,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        with self._lock:
            data = self._load_holdings()
            data.setdefault("dividends", []).append(dividend)
            self._save_holdings(data)

        # 更新净值
        self._on_dividend(amount, stock_name, date)
        return dividend

    def delete_dividend(self, div_id: str) -> bool:
        """删除分红记录"""
        with self._lock:
            data = self._load_holdings()
            dividends = data.get("dividends", [])
            new_dividends = [d for d in dividends if d.get("id") != div_id]
            if len(new_dividends) == len(dividends):
                return False
            data["dividends"] = new_dividends
            self._save_holdings(data)
        return True

    def _on_dividend(self, amount: float, stock_name: str, date: str | None = None):
        """分红后自动更新净值"""
        with self._lock:
            nav = self._load_nav()
            if not nav.get("records"):
                return
            new_nav = round(nav["current_nav"] + amount, 2)
            nav["current_nav"] = new_nav
            nav["records"].append(
                {
                    "date": date or datetime.now().strftime("%Y-%m-%d"),
                    "nav": new_nav,
                    "type": "dividend",
                    "amount": round(amount, 2),
                    "note": f"{stock_name}分红",
                }
            )
            self._save_nav(nav)

    def _sync_dividend_to_list(self, data: dict, stock_name: str, stock_code: str, amount: float, date: str, trade_id: str):
        """将清仓/减仓时的分红同步写入分红列表"""
        div_id = f"d_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
        dividend = {
            "id": div_id,
            "stock_name": stock_name,
            "stock_code": stock_code,
            "amount": round(amount, 2),
            "date": date,
            "trade_id": trade_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        data.setdefault("dividends", []).append(dividend)

    def _sync_dividend_on_edit(self, data: dict, trade: dict):
        """编辑清仓记录时，同步分红到分红列表"""
        trade_id = trade.get("id", "")
        dividend_amount = trade.get("dividend", 0) or 0
        dividends = data.setdefault("dividends", [])

        # 查找已有的关联分红记录
        existing = None
        for d in dividends:
            if d.get("trade_id") == trade_id:
                existing = d
                break

        if dividend_amount > 0:
            if existing:
                # 更新已有记录
                existing["amount"] = round(dividend_amount, 2)
                existing["date"] = trade.get("sell_date", "")
            else:
                # 新建记录
                self._sync_dividend_to_list(
                    data, trade["stock_name"], trade["stock_code"],
                    dividend_amount, trade.get("sell_date", ""), trade_id
                )
        elif existing:
            # 分红为0，删除对应记录
            dividends.remove(existing)

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

    def edit_closed_trade(self, trade_id: str, fields: dict) -> dict | None:
        """编辑已清仓交易，自动更新净值"""
        allowed = {"sell_date", "sell_price", "sell_strategy", "dividend"}
        with self._lock:
            data = self._load_holdings()
            trade = None
            for t in data.get("trades", []):
                if t.get("id") == trade_id and t.get("status") in ("closed", "partial"):
                    trade = t
                    break
            if trade is None:
                return None

            for k, v in fields.items():
                if k in allowed and v is not None:
                    if k == "sell_price":
                        trade[k] = round(float(v), 3)
                    elif k == "dividend":
                        trade[k] = round(float(v), 2)
                    else:
                        trade[k] = v

            # 重算盈亏
            cost_price = trade["cost_price"]
            shares = trade["shares"]
            sell_price = trade["sell_price"]
            total_cost = cost_price * shares
            trade["pnl"] = round((sell_price - cost_price) * shares, 2)
            trade["pnl_pct"] = round(trade["pnl"] / total_cost * 100, 2) if total_cost else 0.0

            # 同步分红到分红列表
            self._sync_dividend_on_edit(data, trade)

            self._save_holdings(data)

        # 更新净值记录
        self._update_nav_for_trade(trade)
        return trade

    def edit_dividend_record(self, div_id: str, fields: dict) -> dict | None:
        """编辑分红记录，自动更新净值"""
        allowed = {"amount", "date"}
        with self._lock:
            data = self._load_holdings()
            dividend = None
            for d in data.get("dividends", []):
                if d.get("id") == div_id:
                    dividend = d
                    break
            if dividend is None:
                return None

            old_amount = dividend.get("amount", 0)
            old_date = dividend.get("date", "")

            for k, v in fields.items():
                if k in allowed and v is not None:
                    if k == "amount":
                        dividend[k] = round(float(v), 2)
                    else:
                        dividend[k] = v

            # 同步到关联的交易记录
            trade_id = dividend.get("trade_id")
            if trade_id:
                for t in data.get("trades", []):
                    if t.get("id") == trade_id:
                        t["dividend"] = dividend.get("amount", 0)
                        break

            self._save_holdings(data)

        # 更新净值记录
        self._update_nav_for_dividend(dividend, old_amount, old_date)
        return dividend

    def _update_nav_for_trade(self, trade: dict):
        """根据交易记录更新对应的净值记录并重算后续"""
        with self._lock:
            nav = self._load_nav()
            records = nav.get("records", [])
            trade_id = trade.get("id", "")
            pnl = trade.get("pnl", 0) or 0
            dividend = trade.get("dividend", 0) or 0
            sell_date = trade.get("sell_date", "")

            # 找到对应的净值记录
            idx = -1
            for i, r in enumerate(records):
                if r.get("trade_id") == trade_id:
                    idx = i
                    break

            if idx >= 0:
                # 更新已有记录
                total = round(pnl + dividend, 2)
                note = f"清仓{trade['stock_name']}"
                if dividend > 0:
                    note += f"(含分红{dividend})"
                records[idx]["date"] = sell_date
                records[idx]["pnl"] = round(pnl, 2)
                records[idx]["dividend"] = round(dividend, 2)
                records[idx]["note"] = note
                self._recalc_nav_from(nav, idx)
            self._save_nav(nav)

    def _update_nav_for_dividend(self, dividend: dict, old_amount: float, old_date: str):
        """根据分红记录更新对应的净值记录并重算后续"""
        with self._lock:
            nav = self._load_nav()
            records = nav.get("records", [])
            new_amount = dividend.get("amount", 0)
            new_date = dividend.get("date", "")
            stock_name = dividend.get("stock_name", "")

            # 找到对应的净值记录（按类型和金额匹配）
            idx = -1
            for i, r in enumerate(records):
                if r.get("type") == "dividend" and r.get("amount") == old_amount and r.get("date") == old_date:
                    idx = i
                    break

            if idx >= 0:
                records[idx]["date"] = new_date
                records[idx]["amount"] = round(new_amount, 2)
                records[idx]["note"] = f"{stock_name}分红"
                self._recalc_nav_from(nav, idx)
            self._save_nav(nav)

    def _recalc_nav_from(self, nav: dict, start_idx: int):
        """从指定索引开始重算净值"""
        records = nav.get("records", [])
        if start_idx < 0 or start_idx >= len(records):
            return

        # 找到前一条记录的 nav 作为基准
        base_nav = records[start_idx - 1]["nav"] if start_idx > 0 else nav.get("initial_nav", 0)

        for i in range(start_idx, len(records)):
            r = records[i]
            if r["type"] == "init":
                continue
            elif r["type"] in ("deposit", "withdraw"):
                # 出入金：基于前一条 + 金额变化
                amount = r.get("amount", 0)
                if r["type"] == "deposit":
                    r["nav"] = round(base_nav + amount, 2)
                else:
                    r["nav"] = round(base_nav - amount, 2)
            elif r["type"] == "close":
                pnl = r.get("pnl", 0) or 0
                div = r.get("dividend", 0) or 0
                r["nav"] = round(base_nav + pnl + div, 2)
            elif r["type"] == "dividend":
                amount = r.get("amount", 0)
                r["nav"] = round(base_nav + amount, 2)
            base_nav = r["nav"]

        nav["current_nav"] = records[-1]["nav"] if records else nav.get("initial_nav", 0)

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

    def _on_trade_close(self, trade_id: str, pnl: float, stock_name: str, dividend: float = 0.0, date: str | None = None):
        """清仓/减仓后自动更新净值（含分红）"""
        with self._lock:
            nav = self._load_nav()
            if not nav.get("records"):
                return
            total = round(pnl + dividend, 2)
            new_nav = round(nav["current_nav"] + total, 2)
            nav["current_nav"] = new_nav
            note = f"清仓{stock_name}"
            if dividend > 0:
                note += f"(含分红{dividend})"
            nav["records"].append(
                {
                    "date": date or datetime.now().strftime("%Y-%m-%d"),
                    "nav": new_nav,
                    "type": "close",
                    "trade_id": trade_id,
                    "pnl": round(pnl, 2),
                    "dividend": round(dividend, 2),
                    "note": note,
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
        return {"trades": [], "strategies": {"buy": [], "sell": []}, "dividends": []}

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
