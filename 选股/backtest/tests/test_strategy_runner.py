"""
Tests for BacktestStrategyRunner
"""

import unittest
from unittest.mock import MagicMock, patch

from 选股.backtest.strategy_runner import BacktestStrategyRunner


class TestExtractFromInd(unittest.TestCase):
    """_extract_from_ind 静态方法测试"""

    def test_simple_key(self):
        ind = {"rsi": 55.0}
        spec = {"key": "rsi"}
        assert BacktestStrategyRunner._extract_from_ind(ind, spec) == 55.0

    def test_nested_key(self):
        ind = {"macd": {"dif": 0.12, "dea": 0.08}}
        spec = {"key": "macd_dif", "source": "macd.dif"}
        assert BacktestStrategyRunner._extract_from_ind(ind, spec) == 0.12

    def test_list_value_returns_last(self):
        ind = {"ma5": [10.0, 10.5, 11.0]}
        spec = {"key": "ma5"}
        assert BacktestStrategyRunner._extract_from_ind(ind, spec) == 11.0

    def test_list_value_empty(self):
        """空列表不做取 [-1] 操作，原样返回（与 scanner.py 一致）"""
        ind = {"ma5": []}
        spec = {"key": "ma5"}
        assert BacktestStrategyRunner._extract_from_ind(ind, spec) == []

    def test_missing_key(self):
        ind = {"rsi": 55.0}
        spec = {"key": "macd"}
        assert BacktestStrategyRunner._extract_from_ind(ind, spec) is None

    def test_deep_nested_path(self):
        ind = {"boll": {"upper": {"values": [100, 105, 110]}}}
        spec = {"key": "boll_upper", "source": "boll.upper.values"}
        assert BacktestStrategyRunner._extract_from_ind(ind, spec) == 110

    def test_intermediate_not_dict(self):
        """路径中间节点不是 dict 时应返回 None"""
        ind = {"boll": [1, 2, 3]}
        spec = {"key": "boll_upper", "source": "boll.upper"}
        assert BacktestStrategyRunner._extract_from_ind(ind, spec) is None

    def test_key_fallback_when_no_source(self):
        """spec 中没有 source 时应使用 key 作为路径"""
        ind = {"close": 10.5}
        spec = {"key": "close"}
        assert BacktestStrategyRunner._extract_from_ind(ind, spec) == 10.5


class TestRunWithEmptyData(unittest.TestCase):
    """run 方法对空数据的处理"""

    def _make_runner_with_mock_provider(self, sliced_data: dict):
        """创建带有 mock strategy 和 data_provider 的 runner"""
        runner = object.__new__(BacktestStrategyRunner)

        # mock strategy
        mock_strategy = MagicMock()
        mock_strategy.EXCLUSION_FILTERS = {}
        mock_strategy.CRITERIA = {}
        mock_strategy.LATEST_INFO_EXTRA = []
        mock_strategy.RESULT_INDICATORS = []
        runner.strategy = mock_strategy

        # mock data_provider
        mock_dp = MagicMock()
        mock_dp.slice_all_klines.return_value = sliced_data
        runner.data_provider = mock_dp

        return runner

    def test_empty_sliced_data_returns_empty(self):
        runner = self._make_runner_with_mock_provider({})
        result = runner.run("2025-06-01")
        assert result == []

    def test_stock_with_wrong_date_skipped(self):
        """当 klines[-1].date != as_of_date 时应跳过该股票"""
        sliced = {
            "000001": {
                "name": "平安银行",
                "klines": [
                    {"date": "2025-05-30", "open": 10, "close": 11, "high": 12,
                     "low": 9, "volume": 100, "pct_chg": 1.0},
                ],
                "closes": [11],
            }
        }
        runner = self._make_runner_with_mock_provider(sliced)
        result = runner.run("2025-06-01")
        assert result == []

    def test_build_indicators_error_skipped(self):
        """build_indicators 抛异常时应跳过"""
        sliced = {
            "000001": {
                "name": "平安银行",
                "klines": [
                    {"date": "2025-06-01", "open": 10, "close": 11, "high": 12,
                     "low": 9, "volume": 100, "pct_chg": 1.0},
                ],
                "closes": [11],
            }
        }
        runner = self._make_runner_with_mock_provider(sliced)
        runner.strategy.build_indicators.side_effect = ValueError("no data")
        result = runner.run("2025-06-01")
        assert result == []

    def test_indicators_error_flag_skipped(self):
        """ind 含 _error=True 时应跳过"""
        sliced = {
            "000001": {
                "name": "平安银行",
                "klines": [
                    {"date": "2025-06-01", "open": 10, "close": 11, "high": 12,
                     "low": 9, "volume": 100, "pct_chg": 1.0},
                ],
                "closes": [11],
            }
        }
        runner = self._make_runner_with_mock_provider(sliced)
        runner.strategy.build_indicators.return_value = {"_error": True}
        result = runner.run("2025-06-01")
        assert result == []


class TestCallCriterion(unittest.TestCase):
    """_call_criterion 静态方法测试"""

    def test_func_without_klines(self):
        """func(ind, weight, params) 形式"""
        def my_criterion(ind, weight, params):
            return 5, {"ok": True}

        score, detail = BacktestStrategyRunner._call_criterion(
            my_criterion, {"rsi": 50}, [], 10, {}
        )
        assert score == 5
        assert detail == {"ok": True}

    def test_func_with_klines(self):
        """func(ind, klines, weight, params) 形式"""
        def my_criterion(ind, klines, weight, params):
            return len(klines), {"count": len(klines)}

        klines = [{"date": "2025-01-01"}, {"date": "2025-01-02"}]
        score, detail = BacktestStrategyRunner._call_criterion(
            my_criterion, {}, klines, 10, {}
        )
        assert score == 2
        assert detail == {"count": 2}


if __name__ == "__main__":
    unittest.main()
