"""BacktestEngine 单元测试。"""
import unittest
from unittest.mock import patch, MagicMock

from 选股.backtest.engine import BacktestEngine


class TestBacktestEngineConfig(unittest.TestCase):
    """测试 _get_config 返回正确的配置字典。"""

    def test_default_config(self):
        engine = BacktestEngine()
        config = engine._get_config()
        self.assertEqual(config["strategy_name"], "b1")
        self.assertEqual(config["pool_name"], "沪深300")
        self.assertEqual(config["top_n"], 10)
        self.assertEqual(config["min_score"], 25)
        self.assertEqual(config["holding_days"], 3)
        self.assertEqual(config["initial_capital"], 100000.0)

    def test_custom_config(self):
        engine = BacktestEngine(
            strategy_name="momentum",
            pool_name="中证500",
            top_n=5,
            min_score=30,
            holding_days=10,
            initial_capital=200000.0,
            data_count=300,
        )
        config = engine._get_config()
        self.assertEqual(config["strategy_name"], "momentum")
        self.assertEqual(config["pool_name"], "中证500")
        self.assertEqual(config["top_n"], 5)
        self.assertEqual(config["min_score"], 30)
        self.assertEqual(config["holding_days"], 10)
        self.assertEqual(config["initial_capital"], 200000.0)
        # data_count 不在 config 中
        self.assertNotIn("data_count", config)

    def test_config_keys(self):
        engine = BacktestEngine()
        expected_keys = {
            "strategy_name", "pool_name", "top_n",
            "min_score", "holding_days", "initial_capital",
        }
        self.assertEqual(set(engine._get_config().keys()), expected_keys)


class TestEnsureData(unittest.TestCase):
    """测试 _ensure_data 不会重复加载数据。"""

    @patch("选股.backtest.engine.BacktestEngine._ensure_data")
    def test_ensure_data_called_once(self, mock_ensure):
        """多次调用 run 时 _ensure_data 只被调用对应次数。"""
        engine = BacktestEngine()
        # 模拟 _ensure_data 什么都不做
        mock_ensure.return_value = None
        engine._ensure_data()
        engine._ensure_data()
        self.assertEqual(mock_ensure.call_count, 2)

    def test_ensure_data_idempotent(self):
        """_ensure_data 在 _data_loaded=True 时跳过加载。"""
        engine = BacktestEngine()
        # 手动模拟加载完成后的行为
        engine._data_loaded = True

        # 调用 _ensure_data 不应触发任何 import
        with patch("builtins.__import__") as mock_import:
            engine._ensure_data()
            mock_import.assert_not_called()

        self.assertTrue(engine._data_loaded)
        self.assertIsNone(engine._provider)
        self.assertIsNone(engine._runner)

    @patch("选股.backtest.strategy_runner.BacktestStrategyRunner")
    @patch("选股.backtest.data_provider.DataProvider")
    def test_ensure_data_loads_once(self, mock_dp_cls, mock_runner_cls):
        """首次调用 _ensure_data 会加载 provider 和 runner。"""
        mock_provider = MagicMock()
        mock_dp_cls.from_pool.return_value = mock_provider

        engine = BacktestEngine(strategy_name="test", pool_name="test_pool", data_count=100)

        self.assertFalse(engine._data_loaded)
        self.assertIsNone(engine._provider)
        self.assertIsNone(engine._runner)

        # 使用 patch 拦截 import
        with patch.dict("sys.modules", {
            "选股.backtest.data_provider": MagicMock(DataProvider=mock_dp_cls),
            "选股.backtest.strategy_runner": MagicMock(BacktestStrategyRunner=mock_runner_cls),
        }):
            engine._ensure_data()

        self.assertTrue(engine._data_loaded)
        mock_dp_cls.from_pool.assert_called_once_with("test_pool", count=100)


if __name__ == "__main__":
    unittest.main()
