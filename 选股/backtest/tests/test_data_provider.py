"""
Tests for DataProvider and TradingCalendar
"""

import unittest

from 选股.backtest.data_provider import TradingCalendar, DataProvider


class TestTradingCalendar(unittest.TestCase):
    """TradingCalendar 测试"""

    def _make_klines(self, dates: list[str]) -> list[dict]:
        return [{"date": d, "open": 10, "close": 11, "high": 12, "low": 9,
                 "volume": 100, "amount": 1000} for d in dates]

    def test_get_dates_returns_all(self):
        dates = ["2025-01-01", "2025-01-02", "2025-01-03"]
        cal = TradingCalendar(self._make_klines(dates))
        assert cal.get_dates() == dates

    def test_get_dates_skip_first(self):
        dates = ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04"]
        cal = TradingCalendar(self._make_klines(dates))
        assert cal.get_dates(skip_first=2) == ["2025-01-03", "2025-01-04"]

    def test_get_dates_skip_all(self):
        dates = ["2025-01-01", "2025-01-02"]
        cal = TradingCalendar(self._make_klines(dates))
        assert cal.get_dates(skip_first=2) == []

    def test_get_dates_skip_zero(self):
        dates = ["2025-01-01"]
        cal = TradingCalendar(self._make_klines(dates))
        assert cal.get_dates(skip_first=0) == ["2025-01-01"]


class TestDataProvider(unittest.TestCase):
    """DataProvider 测试"""

    def _make_stock_data(self) -> dict:
        """构造两只股票的测试数据"""
        dates_a = [f"2025-01-{d:02d}" for d in range(1, 21)]
        klines_a = [{"date": d, "open": 10, "close": 11, "high": 12, "low": 9,
                      "volume": 100, "amount": 1000} for d in dates_a]

        dates_b = [f"2025-01-{d:02d}" for d in range(1, 21)]
        klines_b = [{"date": d, "open": 20, "close": 21, "high": 22, "low": 19,
                      "volume": 200, "amount": 2000} for d in dates_b]

        return {
            "000001": {"name": "平安银行", "klines": klines_a},
            "000002": {"name": "万科A", "klines": klines_b},
        }

    def test_get_codes(self):
        dp = DataProvider(self._make_stock_data())
        codes = dp.get_codes()
        assert set(codes) == {"000001", "000002"}

    def test_get_stock_info_existing(self):
        dp = DataProvider(self._make_stock_data())
        info = dp.get_stock_info("000001")
        assert info is not None
        assert info["name"] == "平安银行"
        assert len(info["klines"]) == 20

    def test_get_stock_info_nonexistent(self):
        dp = DataProvider(self._make_stock_data())
        assert dp.get_stock_info("999999") is None

    def test_slice_klines_exact_match(self):
        dp = DataProvider(self._make_stock_data())
        sliced = dp.slice_klines("000001", "2025-01-10")
        assert len(sliced) == 10
        assert sliced[-1]["date"] == "2025-01-10"

    def test_slice_klines_before_first(self):
        dp = DataProvider(self._make_stock_data())
        sliced = dp.slice_klines("000001", "2024-12-31")
        assert len(sliced) == 0

    def test_slice_klines_after_last(self):
        dp = DataProvider(self._make_stock_data())
        sliced = dp.slice_klines("000001", "2025-12-31")
        assert len(sliced) == 20

    def test_slice_klines_nonexistent_code(self):
        dp = DataProvider(self._make_stock_data())
        sliced = dp.slice_klines("999999", "2025-01-10")
        assert sliced == []

    def test_slice_klines_early_break(self):
        """验证利用排序特性，超出日期后不再遍历"""
        dp = DataProvider(self._make_stock_data())
        # 取第1天，后面19天应被跳过
        sliced = dp.slice_klines("000001", "2025-01-01")
        assert len(sliced) == 1
        assert sliced[0]["date"] == "2025-01-01"

    def test_get_calendar_default(self):
        dp = DataProvider(self._make_stock_data())
        cal = dp.get_calendar()
        assert len(cal.dates) == 20

    def test_get_calendar_with_sample_code(self):
        dp = DataProvider(self._make_stock_data())
        cal = dp.get_calendar(sample_code="000002")
        assert cal.dates[0] == "2025-01-01"
        assert len(cal.dates) == 20
