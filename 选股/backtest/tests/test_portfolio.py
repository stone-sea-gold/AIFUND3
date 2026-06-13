"""PortfolioSimulator 资金管理版单元测试"""

import unittest
from 选股.backtest.portfolio import PortfolioSimulator, Position


class TestPosition(unittest.TestCase):
    def test_position_fields(self):
        pos = Position(
            code="000001", name="平安银行", buy_date="2025-01-02",
            buy_price=10.0, shares=1000, cost=10000.0, select_date="2025-01-01"
        )
        self.assertEqual(pos.status, "open")
        self.assertEqual(pos.buy_day_close, 0.0)


class TestEmptySimulation(unittest.TestCase):
    def test_no_selections(self):
        portfolio = PortfolioSimulator(initial_capital=100000, top_n=5, holding_days=3)
        portfolio.run(
            all_dates=["2025-01-02", "2025-01-03"],
            daily_data={},
            selection_by_date={},
        )
        self.assertEqual(len(portfolio.closed_trades), 0)
        self.assertEqual(portfolio.cash, 100000)
        self.assertEqual(portfolio.metrics["total_return_pct"], 0.0)


class TestBuyAndSell(unittest.TestCase):
    def test_buy_at_next_day_open(self):
        """T日选股 → T+1日开盘价买入"""
        portfolio = PortfolioSimulator(initial_capital=100000, top_n=1, holding_days=3)

        all_dates = ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08"]
        daily_data = {
            "2025-01-02": {},
            "2025-01-03": {"000001": {"open": 10.0, "close": 10.5, "high": 11.0, "low": 9.8}},
            "2025-01-06": {"000001": {"open": 10.6, "close": 10.8, "high": 11.0, "low": 10.4}},
            "2025-01-07": {"000001": {"open": 10.9, "close": 11.0, "high": 11.2, "low": 10.7}},
            "2025-01-08": {"000001": {"open": 11.1, "close": 11.5, "high": 11.8, "low": 10.9}},
        }
        selection_by_date = {
            "2025-01-02": [
                {"code": "000001", "name": "平安银行", "score": 80,
                 "price": 10.0, "buy_price": 10.0}
            ],
        }

        portfolio.run(all_dates, daily_data, selection_by_date)

        self.assertEqual(len(portfolio.closed_trades), 1)
        trade = portfolio.closed_trades[0]
        self.assertEqual(trade["buy_date"], "2025-01-03")
        self.assertEqual(trade["buy_price"], 10.0)
        self.assertEqual(trade["sell_date"], "2025-01-08")
        self.assertEqual(trade["sell_price"], 11.5)
        self.assertEqual(trade["reason"], "take_profit")

    def test_cash_constraint(self):
        """资金不足时不能买入"""
        portfolio = PortfolioSimulator(initial_capital=5000, top_n=5, holding_days=3)

        all_dates = ["2025-01-02", "2025-01-03"]
        daily_data = {
            "2025-01-02": {},
            "2025-01-03": {
                "000001": {"open": 10.0, "close": 10.0, "high": 10.0, "low": 10.0},
                "000002": {"open": 100.0, "close": 100.0, "high": 100.0, "low": 100.0},
            },
        }
        # 5000元本金，每只分配1000元，000002价格100元买不起100股
        selection_by_date = {
            "2025-01-02": [
                {"code": "000002", "name": "高价股", "score": 90,
                 "price": 100.0, "buy_price": 100.0},
                {"code": "000001", "name": "低价股", "score": 80,
                 "price": 10.0, "buy_price": 10.0},
            ],
        }

        portfolio.run(all_dates, daily_data, selection_by_date)

        # 000002 买不起（100元×100股=10000 > 5000/5=1000），000001 能买
        bought_codes = [t["code"] for t in portfolio.closed_trades]
        self.assertNotIn("000002", bought_codes)


class TestStopLoss(unittest.TestCase):
    def test_stop_loss_triggered(self):
        """T+1收盘亏损>3% → T+2开盘止损"""
        portfolio = PortfolioSimulator(
            initial_capital=100000, top_n=1, holding_days=3,
            stop_loss_params={"stop_loss_pct": 3.0, "gap_up_pct": 4.0},
        )

        all_dates = ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"]
        daily_data = {
            "2025-01-02": {},
            "2025-01-03": {"000001": {"open": 10.0, "close": 9.5, "high": 10.1, "low": 9.4}},
            # 买入价10.0，收盘9.5，亏损5% > 3% → 标记止损
            "2025-01-06": {"000001": {"open": 9.3, "close": 9.2, "high": 9.5, "low": 9.1}},
            # 开盘9.3 < 9.5 × 1.04 = 9.88 → 不高开 → 止损卖出
            "2025-01-07": {"000001": {"open": 9.0, "close": 9.1, "high": 9.2, "low": 8.9}},
        }
        selection_by_date = {
            "2025-01-02": [
                {"code": "000001", "name": "测试股", "score": 80,
                 "price": 10.0, "buy_price": 10.0},
            ],
        }

        portfolio.run(all_dates, daily_data, selection_by_date)

        self.assertEqual(len(portfolio.closed_trades), 1)
        trade = portfolio.closed_trades[0]
        self.assertEqual(trade["reason"], "stop_loss")
        self.assertEqual(trade["sell_date"], "2025-01-06")
        self.assertEqual(trade["sell_price"], 9.3)

    def test_gap_up_saves_position(self):
        """T+1收盘亏损>3% → T+2高开≥4% → 取消止损"""
        portfolio = PortfolioSimulator(
            initial_capital=100000, top_n=1, holding_days=3,
            stop_loss_params={"stop_loss_pct": 3.0, "gap_up_pct": 4.0},
        )

        all_dates = ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08"]
        daily_data = {
            "2025-01-02": {},
            "2025-01-03": {"000001": {"open": 10.0, "close": 9.5, "high": 10.1, "low": 9.4}},
            # 买入10.0，收盘9.5，亏损5% > 3% → 标记止损
            "2025-01-06": {"000001": {"open": 10.0, "close": 10.2, "high": 10.3, "low": 9.8}},
            # 开盘10.0 >= 9.5 × 1.04 = 9.88 → 高开 → 取消止损
            "2025-01-07": {"000001": {"open": 10.3, "close": 10.5, "high": 10.6, "low": 10.1}},
            "2025-01-08": {"000001": {"open": 10.6, "close": 11.0, "high": 11.2, "low": 10.4}},
        }
        selection_by_date = {
            "2025-01-02": [
                {"code": "000001", "name": "测试股", "score": 80,
                 "price": 10.0, "buy_price": 10.0},
            ],
        }

        portfolio.run(all_dates, daily_data, selection_by_date)

        self.assertEqual(len(portfolio.closed_trades), 1)
        trade = portfolio.closed_trades[0]
        self.assertEqual(trade["reason"], "take_profit")  # 正常到期，非止损


class TestNAV(unittest.TestCase):
    def test_nav_history_recorded(self):
        portfolio = PortfolioSimulator(initial_capital=100000, top_n=1, holding_days=1)

        all_dates = ["2025-01-02", "2025-01-03", "2025-01-06"]
        daily_data = {
            "2025-01-02": {},
            "2025-01-03": {"000001": {"open": 10.0, "close": 11.0, "high": 11.0, "low": 10.0}},
            "2025-01-06": {"000001": {"open": 11.0, "close": 12.0, "high": 12.0, "low": 11.0}},
        }
        selection_by_date = {
            "2025-01-02": [
                {"code": "000001", "name": "测试", "score": 80,
                 "price": 10.0, "buy_price": 10.0},
            ],
        }

        portfolio.run(all_dates, daily_data, selection_by_date)

        self.assertEqual(len(portfolio.nav_history), 3)
        # 第一天：现金100000，无持仓
        self.assertEqual(portfolio.nav_history[0]["nav"], 100000)
        # 最后一天：已卖出，现金应增加
        self.assertGreater(portfolio.nav_history[-1]["nav"], 100000)


class TestMetrics(unittest.TestCase):
    def test_metrics_with_trades(self):
        portfolio = PortfolioSimulator(initial_capital=100000, top_n=1, holding_days=1)

        all_dates = ["2025-01-02", "2025-01-03", "2025-01-06"]
        daily_data = {
            "2025-01-02": {},
            "2025-01-03": {"000001": {"open": 10.0, "close": 11.0, "high": 11.0, "low": 10.0}},
            "2025-01-06": {"000001": {"open": 11.0, "close": 12.0, "high": 12.0, "low": 11.0}},
        }
        selection_by_date = {
            "2025-01-02": [
                {"code": "000001", "name": "测试", "score": 80,
                 "price": 10.0, "buy_price": 10.0},
            ],
        }

        portfolio.run(all_dates, daily_data, selection_by_date)

        m = portfolio.metrics
        self.assertEqual(m["initial_capital"], 100000)
        self.assertGreater(m["final_nav"], 100000)
        self.assertGreater(m["total_return_pct"], 0)
        self.assertEqual(m["wins"], 1)
        self.assertEqual(m["losses"], 0)
        self.assertEqual(m["win_rate"], 100.0)
        self.assertEqual(m["stop_loss_count"], 0)


if __name__ == "__main__":
    unittest.main()
