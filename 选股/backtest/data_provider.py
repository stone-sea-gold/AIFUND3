"""
DataProvider - 回测数据提供者

从股票池加载K线数据，提供按日期切片的能力。
"""

from __future__ import annotations

from typing import Any


class TradingCalendar:
    """从K线数据提取的交易日历"""

    def __init__(self, klines: list[dict]):
        self.dates: list[str] = [k["date"] for k in klines]

    def get_dates(self, skip_first: int = 0) -> list[str]:
        """返回交易日列表，可跳过前 N 根"""
        return self.dates[skip_first:]


class DataProvider:
    """回测数据提供者"""

    def __init__(self, stock_data: dict[str, dict[str, Any]]):
        """
        Args:
            stock_data: {code: {"name": str, "klines": list[dict]}}
                        klines 已按日期升序排列
        """
        self._stock_data = stock_data

    @classmethod
    def from_pool(cls, pool_name: str = "沪深300", count: int = 500,
                  use_cache: bool = True) -> "DataProvider":
        """从股票池构建 DataProvider"""
        from 选股.pool import get_stock_pool, filter_stocks

        stocks = get_stock_pool(pool_name, use_cache=use_cache)
        stocks = filter_stocks(stocks)
        return cls._load_all_klines(stocks, count, use_cache)

    @classmethod
    def _load_all_klines(cls, stocks: list[tuple[str, str]], count: int,
                         use_cache: bool) -> "DataProvider":
        """遍历股票列表加载K线，仅保留 >= 120 根的标的"""
        from 选股.kline_source import get_klines

        stock_data: dict[str, dict[str, Any]] = {}
        total = len(stocks)

        for i, (code, name) in enumerate(stocks, 1):
            try:
                fetched_name, klines = get_klines(code, count=count, period="day")
            except Exception:
                continue

            if len(klines) < 120:
                continue

            display_name = name if name != code else fetched_name
            stock_data[code] = {"name": display_name, "klines": klines}

            if i % 50 == 0 or i == total:
                print(f"  加载进度: {i}/{total} (已纳入 {len(stock_data)} 只)")

        print(f"  加载完成: 共纳入 {len(stock_data)} 只股票 (>= 120 根K线)")
        return cls(stock_data)

    def get_codes(self) -> list[str]:
        """返回所有股票代码"""
        return list(self._stock_data.keys())

    def get_stock_info(self, code: str) -> dict | None:
        """返回单只股票信息 {"name": ..., "klines": [...]}，不存在返回 None"""
        return self._stock_data.get(code)

    def slice_klines(self, code: str, as_of_date: str) -> list[dict]:
        """
        返回 klines 中 date <= as_of_date 的子集。
        利用已排序特性，遇到第一个超出日期的 kline 即 break。
        """
        info = self._stock_data.get(code)
        if info is None:
            return []

        result: list[dict] = []
        for k in info["klines"]:
            if k["date"] <= as_of_date:
                result.append(k)
            else:
                break
        return result

    def slice_all_klines(self, as_of_date: str) -> dict[str, dict]:
        """
        对所有股票按日期切片。

        Returns:
            {code: {"name": ..., "klines": [...], "closes": [...]}}
            仅保留切片后 >= 120 根的标的
        """
        result: dict[str, dict] = {}
        for code, info in self._stock_data.items():
            sliced = self.slice_klines(code, as_of_date)
            if len(sliced) < 120:
                continue
            result[code] = {
                "name": info["name"],
                "klines": sliced,
                "closes": [k["close"] for k in sliced],
            }
        return result

    def get_calendar(self, sample_code: str | None = None) -> TradingCalendar:
        """
        用 sample_code 或第一只股票的 klines 创建 TradingCalendar。
        """
        if sample_code and sample_code in self._stock_data:
            klines = self._stock_data[sample_code]["klines"]
        else:
            first_code = next(iter(self._stock_data))
            klines = self._stock_data[first_code]["klines"]
        return TradingCalendar(klines)

    def get_full_klines(self, code: str) -> list[dict] | None:
        """获取某只股票的完整K线数据（不做切片）"""
        info = self._stock_data.get(code)
        return info["klines"] if info else None

    def build_daily_lookup(self) -> dict[str, dict[str, dict]]:
        """预构建每日行情查找表，O(总K线数)。

        遍历每只股票的K线一次，按日期分组。

        Returns:
            {date: {code: {"open", "close", "high", "low"}}}
        """
        lookup: dict[str, dict[str, dict]] = {}
        for code, info in self._stock_data.items():
            for bar in info["klines"]:
                d = bar["date"]
                if d not in lookup:
                    lookup[d] = {}
                lookup[d][code] = {
                    "open": bar["open"],
                    "close": bar["close"],
                    "high": bar["high"],
                    "low": bar["low"],
                }
        return lookup

    def build_date_index_map(self) -> dict[str, dict[str, int]]:
        """为每只股票建立 日期→K线索引 的映射，O(总K线数)。

        Returns:
            {code: {date: index_in_klines}}
        """
        result: dict[str, dict[str, int]] = {}
        for code, info in self._stock_data.items():
            idx_map: dict[str, int] = {}
            for i, bar in enumerate(info["klines"]):
                idx_map[bar["date"]] = i
            result[code] = idx_map
        return result
