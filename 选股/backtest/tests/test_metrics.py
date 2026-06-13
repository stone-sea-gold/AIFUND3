import math
import unittest

from 选股.backtest.metrics import (
    calc_sharpe_ratio,
    calc_max_drawdown,
    calc_win_rate,
    calc_profit_factor,
    calc_avg_return,
)


# ── calc_sharpe_ratio ──────────────────────────────────────────────

class TestSharpeRatio:
    def test_constant_returns(self):
        """常数收益（无波动）→ 标准差为0 → 返回0"""
        assert calc_sharpe_ratio([0.01, 0.01, 0.01, 0.01]) == 0.0

    def test_positive_returns(self):
        """正收益 → 正夏普"""
        returns = [0.01, 0.02, 0.015, 0.01, 0.02]
        assert calc_sharpe_ratio(returns) > 0

    def test_negative_returns(self):
        """负收益 → 负夏普"""
        returns = [-0.01, -0.02, -0.015, -0.01, -0.02]
        assert calc_sharpe_ratio(returns) < 0

    def test_empty_list(self):
        """空列表 → 0"""
        assert calc_sharpe_ratio([]) == 0.0

    def test_single_element(self):
        """单元素 → 不够计算方差 → 0"""
        assert calc_sharpe_ratio([0.01]) == 0.0


# ── calc_max_drawdown ──────────────────────────────────────────────

class TestMaxDrawdown:
    def test_rising_series(self):
        """持续上涨 → 0回撤"""
        assert calc_max_drawdown([1, 2, 3, 4, 5]) == 0.0

    def test_single_drop(self):
        """从100跌到82 → 18%回撤"""
        result = calc_max_drawdown([100, 82])
        assert result == 18.0

    def test_rise_then_fall(self):
        """先涨后跌"""
        # 100 → 120 → 96, 最大回撤 = (120-96)/120*100 = 20%
        result = calc_max_drawdown([100, 120, 96])
        assert result == 20.0

    def test_empty_list(self):
        assert calc_max_drawdown([]) == 0.0

    def test_single_element(self):
        assert calc_max_drawdown([100]) == 0.0


# ── calc_win_rate ──────────────────────────────────────────────────

class TestWinRate:
    def test_all_winners(self):
        assert calc_win_rate([0.01, 0.02, 0.03]) == 100.0

    def test_all_losers(self):
        assert calc_win_rate([-0.01, -0.02, -0.03]) == 0.0

    def test_mixed(self):
        # 2胜1负
        result = calc_win_rate([0.01, -0.02, 0.03])
        assert abs(result - 66.7) < 0.1

    def test_empty_list(self):
        assert calc_win_rate([]) == 0.0


# ── calc_profit_factor ─────────────────────────────────────────────

class TestProfitFactor:
    def test_only_gains(self):
        """只有盈利 → inf"""
        result = calc_profit_factor([0.01, 0.02, 0.03])
        assert result == float("inf")

    def test_only_losses(self):
        """只有亏损 → 0"""
        assert calc_profit_factor([-0.01, -0.02, -0.03]) == 0.0

    def test_mixed(self):
        """盈亏混合: gains=0.04, losses=0.02 → 2.0"""
        result = calc_profit_factor([0.01, 0.03, -0.02])
        assert result == 2.0

    def test_empty_list(self):
        assert calc_profit_factor([]) == 0.0


# ── calc_avg_return ────────────────────────────────────────────────

class TestAvgReturn:
    def test_empty_list(self):
        assert calc_avg_return([]) == 0.0

    def test_normal_calculation(self):
        # (0.01 + 0.03 - 0.02) / 3 = 0.006667 → round 4 = 0.0067
        result = calc_avg_return([0.01, 0.03, -0.02])
        assert abs(result - 0.0067) < 0.0001

    def test_all_positive(self):
        assert calc_avg_return([0.02, 0.04]) == 0.03
