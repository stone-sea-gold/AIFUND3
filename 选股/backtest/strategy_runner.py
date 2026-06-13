"""
BacktestStrategyRunner -- 回测策略运行器

在指定历史日期运行策略的完整打分流程，核心逻辑与 scanner.scan_one() 一致，
但不从 API 获取数据，而是接收 DataProvider 提供的按日期切片后的数据。
"""

from __future__ import annotations

import inspect
from typing import Any


class BacktestStrategyRunner:
    """在指定历史日期运行策略的完整打分流程"""

    _precomputed: dict[str, dict] | None = None  # 类级别默认值，兼容 object.__new__ 创建

    def __init__(self, strategy_name: str, data_provider):
        self.strategy = self._load_strategy(strategy_name)
        self.data_provider = data_provider
        self._precomputed = None

    def _load_strategy(self, name: str):
        from 选股.strategy_loader import load_strategy
        return load_strategy(name)

    def precompute(self, date_index_map: dict[str, dict[str, int]] | None = None,
                   verbose: bool = True) -> int:
        """预计算所有股票的指标，只算一次。

        Args:
            date_index_map: {code: {date: index}} 由 DataProvider.build_date_index_map() 提供
            verbose: 是否打印进度

        Returns:
            成功预计算的股票数量
        """
        s = self.strategy
        self._precomputed = {}
        codes = self.data_provider.get_codes()
        total = len(codes)

        for i, code in enumerate(codes):
            info = self.data_provider.get_stock_info(code)
            if info is None:
                continue
            klines = info["klines"]
            closes = [k["close"] for k in klines]
            if len(klines) < 120:
                continue
            try:
                ind = s.build_indicators(klines, closes)
            except Exception:
                continue
            if ind.get("_error", False):
                continue

            df = ind.get("_df")
            self._precomputed[code] = {
                "ind": ind,
                "df": df,
                "klines": klines,
                "closes": closes,
                "name": info["name"],
                "date_idx": date_index_map.get(code, {}) if date_index_map else {},
            }

            if verbose and (i + 1) % 200 == 0:
                print(f"  指标预计算: {i+1}/{total}")

        if verbose:
            print(f"  指标预计算完成: {len(self._precomputed)}/{total} 只")
        return len(self._precomputed)

    def run(
        self,
        as_of_date: str,
        top_n: int = 30,
        min_score: int = 25,
        verbose: bool = False,
    ) -> list[dict]:
        """在指定日期运行策略，返回按分数降序的结果

        Args:
            as_of_date: 回测日期，格式 YYYY-MM-DD
            top_n: 返回前 N 只
            min_score: 最低入围分
            verbose: 是否打印进度

        Returns:
            按 score 降序排列的结果列表
        """
        s = self.strategy

        # 快速路径：使用预计算指标
        if self._precomputed is not None:
            return self._run_with_precomputed(as_of_date, top_n, min_score, verbose)

        # 慢路径：实时计算（兼容未预计算的情况）
        sliced = self.data_provider.slice_all_klines(as_of_date)
        if not sliced:
            return []

        results: list[dict] = []
        for code, stock_info in sliced.items():
            klines = stock_info["klines"]
            if not klines or klines[-1]["date"] != as_of_date:
                continue
            result = self._score_stock(code, stock_info["name"], klines, stock_info["closes"],
                                       as_of_date, s, min_score)
            if result:
                results.append(result)

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_n]

    def _run_with_precomputed(self, as_of_date: str, top_n: int,
                              min_score: int, verbose: bool) -> list[dict]:
        """使用预计算指标的快速打分路径"""
        s = self.strategy
        results: list[dict] = []

        for code, cached in self._precomputed.items():
            date_idx = cached["date_idx"]
            idx = date_idx.get(as_of_date)
            if idx is None:
                continue  # 当日无交易

            # 切片到当日
            klines = cached["klines"][:idx + 1]
            if len(klines) < 120:
                continue

            # 构建切片后的 ind（复用预计算的 DataFrame）
            ind = dict(cached["ind"])
            if cached["df"] is not None:
                ind["_df"] = cached["df"].iloc[:idx + 1]

            # 排除过滤
            excluded = False
            for exc_cfg in s.EXCLUSION_FILTERS.values():
                if not exc_cfg.get("enabled", True):
                    continue
                try:
                    if exc_cfg["func"](ind, klines):
                        excluded = True
                        break
                except Exception:
                    excluded = True
                    break
            if excluded:
                continue

            # 打分
            total_score = 0
            details = []
            for crit_cfg in s.CRITERIA.values():
                func = crit_cfg.get("func")
                if func is None:
                    continue
                weight = crit_cfg.get("weight", 0)
                params = crit_cfg.get("params", {})
                try:
                    score, detail = self._call_criterion(func, ind, klines, weight, params)
                except Exception:
                    score, detail = 0, {"reason": "计算异常"}
                if score > 0:
                    total_score += score
                    details.append({
                        "criterion": "",
                        "desc": crit_cfg.get("desc", ""),
                        "score": score,
                        "weight": weight,
                        "detail": detail,
                    })

            if total_score < min_score:
                continue

            live_k = klines[-1]
            latest_info = {
                "date": live_k["date"],
                "close": live_k["close"],
                "pct_chg": live_k["pct_chg"],
                "volume": int(live_k["volume"]),
            }
            for spec in s.LATEST_INFO_EXTRA:
                latest_info[spec["key"]] = self._extract_from_ind(ind, spec)
            result_indicators = {}
            for spec in s.RESULT_INDICATORS:
                result_indicators[spec["key"]] = self._extract_from_ind(ind, spec)

            results.append({
                "code": code,
                "name": cached["name"],
                "score": total_score,
                "details": details,
                "latest_info": latest_info,
                "indicators": result_indicators,
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        if verbose:
            print(f"[{as_of_date}] 入围 {len(results)} 只")
        return results[:top_n]

    def _score_stock(self, code, name, klines, closes, as_of_date, s, min_score) -> dict | None:
        """对单只股票执行完整打分流程（实时计算路径）"""
        try:
            ind = s.build_indicators(klines, closes)
        except Exception:
            return None
        if ind.get("_error", False):
            return None

        for exc_cfg in s.EXCLUSION_FILTERS.values():
            if not exc_cfg.get("enabled", True):
                continue
            try:
                if exc_cfg["func"](ind, klines):
                    return None
            except Exception:
                return None

        total_score = 0
        details = []
        for crit_cfg in s.CRITERIA.values():
            func = crit_cfg.get("func")
            if func is None:
                continue
            weight = crit_cfg.get("weight", 0)
            params = crit_cfg.get("params", {})
            try:
                score, detail = self._call_criterion(func, ind, klines, weight, params)
            except Exception:
                score, detail = 0, {"reason": "计算异常"}
            if score > 0:
                total_score += score
                details.append({"criterion": "", "desc": crit_cfg.get("desc", ""),
                                "score": score, "weight": weight, "detail": detail})

        if total_score < min_score:
            return None

        live_k = klines[-1]
        latest_info = {"date": live_k["date"], "close": live_k["close"],
                       "pct_chg": live_k["pct_chg"], "volume": int(live_k["volume"])}
        for spec in s.LATEST_INFO_EXTRA:
            latest_info[spec["key"]] = self._extract_from_ind(ind, spec)
        result_indicators = {}
        for spec in s.RESULT_INDICATORS:
            result_indicators[spec["key"]] = self._extract_from_ind(ind, spec)

        return {"code": code, "name": name, "score": total_score, "details": details,
                "latest_info": latest_info, "indicators": result_indicators}

    @staticmethod
    def _call_criterion(
        func, ind: dict, klines: list[dict], weight: int, params: dict
    ) -> tuple[int, dict]:
        """调用条件函数，根据函数签名自动传参"""
        sig = inspect.signature(func)
        kwargs: dict[str, Any] = {"ind": ind, "weight": weight, "params": params}
        if "klines" in sig.parameters:
            kwargs["klines"] = klines
        return func(**kwargs)

    @staticmethod
    def _extract_from_ind(ind: dict, spec: dict):
        """按 source 路径从指标字典中提取值

        路径如 'macd.dif' 表示 ind['macd']['dif'][-1]
        """
        source = spec.get("source", spec["key"])
        parts = source.split(".")
        val = ind
        for part in parts:
            if isinstance(val, dict):
                val = val.get(part)
            else:
                return None
        # 若提取到的是列表，取最后一个元素
        if isinstance(val, list) and len(val) > 0:
            val = val[-1]
        return val
