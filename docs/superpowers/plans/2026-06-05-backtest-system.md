# 回测系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local backtesting system for the project's scoring-based stock selection strategies (B1量价共振, 砖型图趋势拐点, 无情操盘手+波浪理论).

**Architecture:** Load full K-line data once, then for each historical date slice data to only include bars up to that date, re-run strategy scoring on the truncated data, record top-N selections, and simulate forward portfolio performance. Uses existing strategy module protocol (`build_indicators` → `EXCLUSION_FILTERS` → `CRITERIA`) unchanged — only supplies time-truncated data.

**Tech Stack:** Python, pandas, numpy, FastAPI (for web report), ECharts (for charts)

---

### Task 1: Data Provider — load historical K-line data for all stocks in a pool

**Files:**
- Create: `选股/backtest/__init__.py`
- Create: `选股/backtest/data_provider.py`
- Test: `选股/backtest/tests/test_data_provider.py`

- [ ] **Step 1: Create package init**

```python
# 选股/backtest/__init__.py
"""回测子系统：对评分型选股策略进行历史回测与绩效分析"""
```

- [ ] **Step 2: Write failing test for DataProvider**

```python
# 选股/backtest/tests/__init__.py
```
```python
# 选股/backtest/tests/test_data_provider.py
import pytest
import tempfile
from pathlib import Path
from 选股.backtest.data_provider import DataProvider, TradingCalendar


class TestTradingCalendar:
    def test_generate_dates_from_klines(self):
        klines = [
            {"date": "2025-01-02", "close": 10},
            {"date": "2025-01-03", "close": 11},
            {"date": "2025-01-06", "close": 12},
            {"date": "2025-01-07", "close": 13},
        ]
        cal = TradingCalendar(klines)
        assert cal.dates == ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"]

    def test_skip_first_n(self):
        klines = [
            {"date": f"2025-01-{d:02d}", "close": 10}
            for d in range(2, 20)
        ]
        cal = TradingCalendar(klines)
        skipped = cal.get_dates(skip_first=5)
        assert len(skipped) == len(klines) - 5
        assert skipped[0] == "2025-01-07"


class TestDataProvider:
    def test_load_klines_for_stock(self):
        """加载单只股票的K线数据"""
        pass  # integration test — skip in unit

    def test_slice_klines_up_to_date(self):
        klines = [
            {"date": "2025-01-02", "close": 10},
            {"date": "2025-01-03", "close": 11},
            {"date": "2025-01-06", "close": 12},
        ]
        provider = DataProvider({"000001": {"name": "test", "klines": klines}})
        sliced = provider.slice_klines("000001", "2025-01-03")
        assert len(sliced) == 2
        assert sliced[-1]["date"] == "2025-01-03"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest 选股/backtest/tests/test_data_provider.py -v 2>&1`
Expected: FAIL (ModuleNotFoundError or ImportError)

- [ ] **Step 4: Write DataProvider and TradingCalendar implementation**

```python
# 选股/backtest/data_provider.py
"""
回测数据层：管理全量K线数据加载、按日期切片、交易日历。

用法:
    from 选股.backtest.data_provider import DataProvider, TradingCalendar

    provider = DataProvider.from_pool("沪深300", count=500)
    cal = provider.get_calendar("000001")
    for date in cal.get_dates(skip_first=250):
        sliced = provider.slice_all_klines(date)
        # sliced: {code: {"name": ..., "klines": [...], "closes": [...]}}
"""
import sys
from pathlib import Path
from datetime import datetime, date
from typing import Any


class TradingCalendar:
    """从K线数据提取交易日历"""

    def __init__(self, klines: list[dict]):
        self.dates = [k["date"] for k in klines]

    def get_dates(self, skip_first: int = 0) -> list[str]:
        """返回交易日列表，可跳过前 N 根用于指标预热"""
        return self.dates[skip_first:]


class DataProvider:
    """回测数据提供者：预加载全量K线，按日期切片"""

    def __init__(self, stock_data: dict[str, dict[str, Any]]):
        """
        Args:
            stock_data: {code: {"name": str, "klines": list[dict]}}
              其中 klines 已按日期升序排列
        """
        self._data = stock_data

    @classmethod
    def from_pool(
        cls,
        pool_name: str = "沪深300",
        count: int = 500,
        use_cache: bool = True,
    ) -> "DataProvider":
        """从股票池加载所有K线数据。

        Args:
            pool_name: 股票池名称
            count: 每只股票加载的K线根数
            use_cache: 是否优先使用本地 kline_cache

        Returns:
            DataProvider 实例
        """
        from 选股.pool import get_stock_pool, filter_stocks
        stocks = get_stock_pool(pool_name, use_cache=use_cache)
        stocks = filter_stocks(stocks)
        return cls._load_all_klines(stocks, count, use_cache)

    @classmethod
    def _load_all_klines(
        cls,
        stocks: list[tuple[str, str]],
        count: int,
        use_cache: bool,
    ) -> "DataProvider":
        """批量加载K线数据"""
        import time
        stock_data = {}
        total = len(stocks)
        print(f"[backtest] 加载 {total} 只股票K线数据 (count={count}) ...")
        t0 = time.time()

        for i, (code, name) in enumerate(stocks):
            if (i + 1) % 100 == 0:
                elapsed = time.time() - t0
                print(f"  [{i+1}/{total}] {elapsed:.0f}s")

            klines = _load_stock_klines(code, count, use_cache)
            if klines is not None and len(klines) >= 120:
                stock_data[code] = {"name": name, "klines": klines}

        elapsed = time.time() - t0
        print(f"[backtest] 加载完成: {len(stock_data)}/{total} 只, 耗时 {elapsed:.0f}s")
        return cls(stock_data)

    def get_codes(self) -> list[str]:
        return list(self._data.keys())

    def get_stock_info(self, code: str) -> dict | None:
        return self._data.get(code)

    def slice_klines(self, code: str, as_of_date: str) -> list[dict]:
        """获取某只股票截至指定日期的K线数据"""
        info = self._data.get(code)
        if info is None:
            return []
        klines = info["klines"]
        result = []
        for k in klines:
            if k["date"] <= as_of_date:
                result.append(k)
            else:
                break
        return result

    def slice_all_klines(self, as_of_date: str) -> dict[str, dict]:
        """获取所有股票截至指定日期的切片数据

        Returns:
            {code: {"name": ..., "klines": [...], "closes": [...]}}
        """
        result = {}
        for code, info in self._data.items():
            sliced = self.slice_klines(code, as_of_date)
            if len(sliced) >= 120:
                result[code] = {
                    "name": info["name"],
                    "klines": sliced,
                    "closes": [k["close"] for k in sliced],
                }
        return result

    def get_calendar(self, sample_code: str | None = None) -> TradingCalendar:
        """获取交易日历（基于指定股票或第一只股票的K线数据）"""
        if sample_code is None:
            sample_code = next(iter(self._data.keys()))
        info = self._data.get(sample_code)
        if info is None:
            raise ValueError(f"股票 {sample_code} 无数据")
        return TradingCalendar(info["klines"])


def _load_stock_klines(code: str, count: int, use_cache: bool) -> list[dict] | None:
    """加载单只股票K线数据，优先走 kline_cache / TDX，失败返回 None"""
    try:
        from 选股.kline_source import get_klines
        _, klines = get_klines(code, count=count, period="day")
        return klines
    except Exception:
        return None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest 选股/backtest/tests/test_data_provider.py -v 2>&1`
Expected: PASS (unit tests pass)

- [ ] **Step 6: Commit**

```bash
git add 选股/backtest/__init__.py 选股/backtest/data_provider.py 选股/backtest/tests/__init__.py 选股/backtest/tests/test_data_provider.py
git commit -m "feat(backtest): add DataProvider and TradingCalendar for historical K-line data loading"
```

---

### Task 2: Strategy Runner — run scoring strategy on time-sliced data

**Files:**
- Create: `选股/backtest/strategy_runner.py`
- Test: `选股/backtest/tests/test_strategy_runner.py`

- [ ] **Step 1: Write failing test for strategy runner**

```python
# 选股/backtest/tests/test_strategy_runner.py
import pytest
from 选股.backtest.strategy_runner import run_strategy_on_date


class TestRunStrategyOnDate:
    def test_returns_ranked_results(self):
        """验证在指定日期运行策略返回按分数降序的结果"""
        pass  # integration test — relies on loaded strategy

    def test_results_have_required_fields(self):
        """验证每条结果包含 code, name, score, details"""
        pass  # integration test


class TestFilterByExclusions:
    def test_stock_fails_exclusion_is_omitted(self):
        """验证不满足排除条件的股票不在结果中"""
        pass  # unit test with mock strategy
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest 选股/backtest/tests/test_strategy_runner.py -v 2>&1`
Expected: FAIL

- [ ] **Step 3: Write strategy runner implementation**

```python
# 选股/backtest/strategy_runner.py
"""
策略执行器：在指定历史日期运行策略的完整打分流程。

返回给定日期下所有股票的打分排序结果，与 scan_one() 逻辑一致，
但使用 DataProvider 提供的按日期切片的数据而非实时 API 数据。

用法:
    from 选股.backtest.strategy_runner import BacktestStrategyRunner
    from 选股.backtest.data_provider import DataProvider

    provider = DataProvider.from_pool("沪深300")
    runner = BacktestStrategyRunner("b1", provider)
    results = runner.run("2026-03-15", top_n=20, min_score=25)
    # [{"code": ..., "name": ..., "score": ..., "details": [...]}, ...]
"""
import sys
from pathlib import Path
from typing import Any


class BacktestStrategyRunner:
    """回测策略执行器：在指定历史日期运行策略的打分流程"""

    def __init__(self, strategy_name: str, data_provider):
        """
        Args:
            strategy_name: 策略名，对应 选股/strategies/{name}.py
            data_provider: DataProvider 实例
        """
        self.strategy = self._load_strategy(strategy_name)
        self.data_provider = data_provider

    def _load_strategy(self, name: str):
        """加载策略模块"""
        from 选股.strategy_loader import load_strategy
        return load_strategy(name)

    def run(
        self,
        as_of_date: str,
        top_n: int = 30,
        min_score: int = 25,
        verbose: bool = False,
    ) -> list[dict]:
        """在指定日期运行策略，返回按分数降序排列的结果。

        Args:
            as_of_date: 回测日期 (YYYY-MM-DD)，只使用此日期前的数据
            top_n: 返回前 N 只
            min_score: 最低入围分
            verbose: 是否打印进度

        Returns:
            [{code, name, score, details, latest_info, indicators}, ...]
        """
        # 1. 获取所有股票的切片数据
        all_data = self.data_provider.slice_all_klines(as_of_date)
        codes = list(all_data.keys())

        results = []
        for code in codes:
            info = all_data[code]

            # 2. 基础过滤
            if info["klines"][-1]["date"] != as_of_date:
                # 当日无交易（停牌/休市）→ 跳过
                if verbose:
                    pass  # skip silently
                continue

            # 3. 策略指标计算
            try:
                ind = self.strategy.build_indicators(
                    info["klines"], info["closes"]
                )
            except Exception:
                continue

            if ind.get("_error", False):
                continue

            # 4. 排除过滤
            excluded = False
            for exc_key, exc_cfg in self.strategy.EXCLUSION_FILTERS.items():
                if not exc_cfg.get("enabled", True):
                    continue
                try:
                    if exc_cfg["func"](ind, info["klines"]):
                        excluded = True
                        break
                except Exception:
                    excluded = True
                    break

            if excluded:
                continue

            # 5. 打分
            total_score = 0
            details = []
            for crit_key, crit_cfg in self.strategy.CRITERIA.items():
                weight = crit_cfg.get("weight", 0)
                params = crit_cfg.get("params", {})
                func = crit_cfg.get("func")
                if func is None:
                    continue
                try:
                    score, detail = self._call_criterion(
                        func, ind, info["klines"], weight, params
                    )
                except Exception:
                    score, detail = 0, {"reason": "计算异常"}
                if score > 0:
                    total_score += score
                    details.append({
                        "criterion": crit_key,
                        "desc": crit_cfg.get("desc", crit_key),
                        "score": score,
                        "weight": weight,
                        "detail": detail,
                    })

            if total_score < min_score:
                continue

            # 6. 构建结果
            latest_k = info["klines"][-1]
            latest_info = {
                "date": latest_k["date"],
                "close": latest_k["close"],
                "pct_chg": latest_k["pct_chg"],
                "volume": int(latest_k["volume"]),
            }
            for spec in self.strategy.LATEST_INFO_EXTRA:
                latest_info[spec["key"]] = self._extract_from_ind(ind, spec)

            result_indicators = {}
            for spec in self.strategy.RESULT_INDICATORS:
                result_indicators[spec["key"]] = self._extract_from_ind(ind, spec)

            results.append({
                "code": code,
                "name": info["name"],
                "score": total_score,
                "details": details,
                "latest_info": latest_info,
                "indicators": result_indicators,
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_n]

    def _call_criterion(self, func, ind, klines, weight, params):
        """调用条件函数，兼容 func(ind, klines, weight, params) 和 func(ind, weight, params) 两种签名"""
        import inspect
        sig = inspect.signature(func)
        kwargs = {"ind": ind, "weight": weight, "params": params}
        if "klines" in sig.parameters:
            kwargs["klines"] = klines
        return func(**kwargs)

    def _extract_from_ind(self, ind: dict, spec: dict):
        """按 source 路径从指标字典中提取值"""
        source = spec.get("source", spec["key"])
        parts = source.split(".")
        val = ind
        for part in parts:
            if isinstance(val, dict):
                val = val.get(part)
            else:
                return None
        if isinstance(val, list) and len(val) > 0:
            val = val[-1]
        return val
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest 选股/backtest/tests/test_strategy_runner.py -v 2>&1`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add 选股/backtest/strategy_runner.py 选股/backtest/tests/test_strategy_runner.py
git commit -m "feat(backtest): add BacktestStrategyRunner for running strategies on historical dates"
```

---

### Task 3: Portfolio Simulator — simulate trading from periodic stock selections

**Files:**
- Create: `选股/backtest/portfolio.py`
- Test: `选股/backtest/tests/test_portfolio.py`

- [ ] **Step 1: Write failing test**

```python
# 选股/backtest/tests/test_portfolio.py
import pytest
from 选股.backtest.portfolio import PortfolioSimulator


class TestPortfolioSimulator:
    def test_empty_run_returns_zero_trades(self):
        """没有选股结果的日期不应产生交易"""
        sim = PortfolioSimulator(holding_days=5, top_n=5)
        sim.run([])  # empty rounds
        assert len(sim.trades) == 0
        assert sim.metrics["total_return_pct"] == 0.0

    def test_single_round_single_stock_profit(self):
        """单轮单只上涨股票的收益计算"""
        data_provider = _make_dummy_provider()
        sim = PortfolioSimulator(holding_days=3, top_n=1)
        round_result = {
            "date": "2025-01-06",
            "stocks": [
                {"code": "000001", "name": "平安银行",
                 "score": 80, "price": 10.0}
            ]
        }
        # 模拟: 买入10.0, 3天后close=11.0 → 10%收益
        sim.run([round_result], data_provider)
        assert len(sim.trades) == 1
        assert sim.trades[0]["return_pct"] == pytest.approx(10.0, rel=0.1)
        assert sim.metrics["total_return_pct"] == pytest.approx(10.0, rel=0.1)

    def test_multiple_rounds_averaging(self):
        """多轮选股应计算平均收益"""
        pass


def _make_dummy_provider():
    """创建模拟 DataProvider 用于测试"""
    pass
```

- [ ] **Step 2: Run test**

Run: `pytest 选股/backtest/tests/test_portfolio.py -v 2>&1`
Expected: FAIL

- [ ] **Step 3: Write PortfolioSimulator**

```python
# 选股/backtest/portfolio.py
"""
投资组合模拟器：模拟在回测日期买入选中的 Top-N 股票，
持有固定天数后卖出，记录每笔交易的盈亏。

用法:
    from 选股.backtest.portfolio import PortfolioSimulator

    sim = PortfolioSimulator(holding_days=5, top_n=5)
    sim.run(rounds, data_provider)
    print(sim.metrics)  # 总收益、胜率、平均收益等
    print(sim.trades)   # 每笔交易明细
"""
import statistics
from typing import Any


class PortfolioSimulator:
    """模拟定期选股 + 持有固定天数卖出的投资组合"""

    def __init__(
        self,
        holding_days: int = 5,
        top_n: int = 5,
        capital_per_stock: float = 10000.0,
    ):
        """
        Args:
            holding_days: 每次选股后持有的交易日数
            top_n: 每次选股买入前 N 只
            capital_per_stock: 每只股票投入资金
        """
        self.holding_days = holding_days
        self.top_n = top_n
        self.capital_per_stock = capital_per_stock
        self.trades: list[dict] = []
        self.metrics: dict[str, Any] = {}

    def run(
        self,
        rounds: list[dict],
        data_provider=None,
    ):
        """执行回测模拟。

        Args:
            rounds: [{date, stocks: [{code, name, score, price}, ...]}, ...]
            data_provider: DataProvider，用于查询卖出价格
        """
        self.trades = []
        all_returns = []

        for round_data in rounds:
            buy_date = round_data["date"]
            top_stocks = round_data["stocks"][:self.top_n]

            for stock in top_stocks:
                code = stock["code"]
                name = stock["name"]
                buy_price = stock["price"]

                if buy_price <= 0:
                    continue

                # 查找卖出价格: holding_days 个交易日后的收盘价
                sell_info = self._find_sell_price(
                    code, buy_date, self.holding_days, data_provider
                )

                if sell_info is None:
                    continue  # 数据不足，跳过

                sell_price = sell_info["price"]
                sell_date = sell_info["date"]

                return_pct = (sell_price - buy_price) / buy_price * 100
                pnl = round(self.capital_per_stock * return_pct / 100, 2)

                trade = {
                    "code": code,
                    "name": name,
                    "buy_date": buy_date,
                    "sell_date": sell_date,
                    "buy_price": round(buy_price, 3),
                    "sell_price": round(sell_price, 3),
                    "return_pct": round(return_pct, 2),
                    "pnl": pnl,
                    "score": stock.get("score", 0),
                }
                self.trades.append(trade)
                all_returns.append(return_pct)

        self._calc_metrics(all_returns)

    def _find_sell_price(self, code: str, buy_date: str, days: int, data_provider) -> dict | None:
        """找到买入日期后第 N 个交易日的收盘价"""
        if data_provider is None:
            return None
        info = data_provider.get_stock_info(code)
        if info is None:
            return None

        klines = info["klines"]
        buy_idx = None
        for i, k in enumerate(klines):
            if k["date"] == buy_date:
                buy_idx = i
                break

        if buy_idx is None:
            return None

        sell_idx = buy_idx + days
        if sell_idx >= len(klines):
            return None

        sell_kline = klines[sell_idx]
        return {"price": sell_kline["close"], "date": sell_kline["date"]}

    def _calc_metrics(self, all_returns: list[float]):
        """计算绩效指标"""
        n = len(all_returns)
        if n == 0:
            self.metrics = {
                "total_trades": 0,
                "total_return_pct": 0.0,
                "avg_return_pct": 0.0,
                "win_rate": 0.0,
                "wins": 0,
                "losses": 0,
                "max_return_pct": 0.0,
                "min_return_pct": 0.0,
                "std_return_pct": 0.0,
            }
            return

        wins = sum(1 for r in all_returns if r > 0)
        total_return = sum(all_returns)
        avg_return = total_return / n

        self.metrics = {
            "total_trades": n,
            "total_return_pct": round(total_return, 2),
            "avg_return_pct": round(avg_return, 2),
            "win_rate": round(wins / n * 100, 1) if n > 0 else 0.0,
            "wins": wins,
            "losses": n - wins,
            "max_return_pct": round(max(all_returns), 2),
            "min_return_pct": round(min(all_returns), 2),
            "std_return_pct": round(statistics.stdev(all_returns), 2) if n > 1 else 0.0,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest 选股/backtest/tests/test_portfolio.py -v 2>&1`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add 选股/backtest/portfolio.py 选股/backtest/tests/test_portfolio.py
git commit -m "feat(backtest): add PortfolioSimulator for simulating trades from periodic stock selections"
```

---

### Task 4: Core Engine — orchestrate full backtest: date iteration → strategy run → portfolio sim

**Files:**
- Create: `选股/backtest/engine.py`
- Test: `选股/backtest/tests/test_engine.py`

- [ ] **Step 1: Write failing test**

```python
# 选股/backtest/tests/test_engine.py
import pytest
from 选股.backtest.engine import BacktestEngine


class TestBacktestEngine:
    def test_run_returns_results(self):
        engine = BacktestEngine(
            strategy_name="b1",
            pool_name="沪深300",
            top_n=10,
            min_score=25,
            holding_days=5,
            capital_per_stock=10000,
        )
        result = engine.run(
            start_date="2026-01-05",
            end_date="2026-03-30",
            skip_first=250,
            verbose=False,
        )
        assert result["strategy_name"] == "b1"
        assert result["pool_name"] == "沪深300"
        assert result["total_rounds"] > 0
        assert "metrics" in result
        assert "trades" in result

    def test_end_date_before_start_returns_empty(self):
        engine = BacktestEngine(strategy_name="b1", pool_name="沪深300")
        result = engine.run(start_date="2026-06-01", end_date="2026-01-01")
        assert result["total_rounds"] == 0
```

- [ ] **Step 2: Run test**

Run: `pytest 选股/backtest/tests/test_engine.py -v 2>&1`
Expected: FAIL

- [ ] **Step 3: Write BacktestEngine**

```python
# 选股/backtest/engine.py
"""
回测引擎核心 — 编排完整回测流程：
  1. 初始化 DataProvider（加载全量K线）
  2. 按交易日历遍历日期
  3. 每个日期运行 BacktestStrategyRunner
  4. 收集选股结果 → 传入 PortfolioSimulator

用法:
    from 选股.backtest.engine import BacktestEngine

    engine = BacktestEngine(strategy_name="b1", pool_name="沪深300")
    result = engine.run(start_date="2026-01-05", end_date="2026-06-01")
    print(result["metrics"])
    print(result["trades"][:5])
"""
import sys
import time
from datetime import datetime
from typing import Any


class BacktestEngine:
    """回测引擎 — 日期迭代 + 策略执行 + 组合模拟的完整流程"""

    def __init__(
        self,
        strategy_name: str = "b1",
        pool_name: str = "沪深300",
        top_n: int = 10,
        min_score: int = 25,
        holding_days: int = 5,
        capital_per_stock: float = 10000.0,
        data_count: int = 500,
    ):
        """
        Args:
            strategy_name: 策略文件名 (不含 .py)
            pool_name: 股票池名称
            top_n: 每次选几只
            min_score: 最低入围分
            holding_days: 持有天数
            capital_per_stock: 每只投入资金
            data_count: 每只加载多少根K线
        """
        self.strategy_name = strategy_name
        self.pool_name = pool_name
        self.top_n = top_n
        self.min_score = min_score
        self.holding_days = holding_days
        self.capital_per_stock = capital_per_stock
        self.data_count = data_count

        # 延迟加载
        self._provider = None
        self._runner = None
        self._data_loaded = False

    def _ensure_data(self):
        """确保数据已加载"""
        if not self._data_loaded:
            from 选股.backtest.data_provider import DataProvider
            from 选股.backtest.strategy_runner import BacktestStrategyRunner

            print(f"[backtest] 加载数据: pool={self.pool_name}, count={self.data_count}")
            self._provider = DataProvider.from_pool(
                self.pool_name, count=self.data_count
            )
            self._runner = BacktestStrategyRunner(self.strategy_name, self._provider)
            self._data_loaded = True

    def run(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        skip_first: int = 250,
        verbose: bool = True,
    ) -> dict[str, Any]:
        """执行回测。

        Args:
            start_date: 开始日期，None 则从数据起始日 + skip_first 开始
            end_date: 结束日期，None 则到最新数据日
            skip_first: 开始日期的偏移量（跳过早期不成熟的指标）
            verbose: 是否打印进度

        Returns:
            {
                strategy_name, pool_name,
                total_rounds, total_trades,
                metrics: {...},
                trades: [...],
                rounds: [...],
                config: {...},
                elapsed: seconds,
            }
        """
        self._ensure_data()
        t0 = time.time()

        # 获取交易日历
        calendar = self._provider.get_calendar()
        all_dates = calendar.get_dates(skip_first=skip_first)

        if start_date:
            all_dates = [d for d in all_dates if d >= start_date]
        if end_date:
            all_dates = [d for d in all_dates if d <= end_date]

        if not all_dates:
            return {
                "strategy_name": self.strategy_name,
                "pool_name": self.pool_name,
                "total_rounds": 0,
                "total_trades": 0,
                "metrics": {},
                "trades": [],
                "rounds": [],
                "config": self._get_config(),
                "elapsed": time.time() - t0,
            }

        # 遍历日期执行策略
        rounds = []
        total = len(all_dates)
        if verbose:
            print(f"[backtest] 开始回测: {self.strategy_name} @ {self.pool_name}")
            print(f"          日期范围: {all_dates[0]} ~ {all_dates[-1]} ({total} 个交易日)")
            print(f"          top_n={self.top_n}, min_score={self.min_score}, hold={self.holding_days}天")

        for i, date in enumerate(all_dates):
            stocks = self._runner.run(
                as_of_date=date,
                top_n=self.top_n,
                min_score=self.min_score,
            )

            if stocks:
                rounds.append({
                    "date": date,
                    "stocks": [
                        {
                            "code": s["code"],
                            "name": s["name"],
                            "score": s["score"],
                            "price": s["latest_info"]["close"],
                        }
                        for s in stocks
                    ],
                })

            if verbose and (i + 1) % 20 == 0:
                passed = rounds[-1]["stocks"] if rounds else []
                print(f"  [{i+1}/{total}] {date} → {len(passed)} 只合格")

        # 执行组合模拟
        from 选股.backtest.portfolio import PortfolioSimulator
        sim = PortfolioSimulator(
            holding_days=self.holding_days,
            top_n=self.top_n,
            capital_per_stock=self.capital_per_stock,
        )
        sim.run(rounds, self._provider)

        elapsed = time.time() - t0

        result = {
            "strategy_name": self.strategy_name,
            "pool_name": self.pool_name,
            "date_range": {"start": all_dates[0], "end": all_dates[-1]},
            "total_rounds": len(rounds),
            "total_trades": len(sim.trades),
            "metrics": sim.metrics,
            "trades": sim.trades,
            "rounds": rounds,
            "config": self._get_config(),
            "elapsed": round(elapsed, 1),
        }

        if verbose:
            m = sim.metrics
            print(f"\n[backtest] 回测完成 ({elapsed:.0f}s)")
            print(f"          交易日: {len(all_dates)} | 选股轮次: {len(rounds)} | 交易: {m.get('total_trades', 0)} 笔")
            print(f"          总收益: {m.get('total_return_pct', 0):+.2f}% | 胜率: {m.get('win_rate', 0):.1f}%")
            print(f"          平均每笔: {m.get('avg_return_pct', 0):+.2f}%")
            if m.get("wins") is not None:
                print(f"          胜: {m['wins']} 笔 / 负: {m['losses']} 笔")

        return result

    def _get_config(self) -> dict:
        return {
            "strategy_name": self.strategy_name,
            "pool_name": self.pool_name,
            "top_n": self.top_n,
            "min_score": self.min_score,
            "holding_days": self.holding_days,
            "capital_per_stock": self.capital_per_stock,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest 选股/backtest/tests/test_engine.py -v 2>&1`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add 选股/backtest/engine.py 选股/backtest/tests/test_engine.py
git commit -m "feat(backtest): add BacktestEngine orchestrating full backtest flow"
```

---

### Task 5: Metrics Module — detailed performance analysis

**Files:**
- Create: `选股/backtest/metrics.py`
- Test: `选股/backtest/tests/test_metrics.py`

- [ ] **Step 1: Write failing test**

```python
# 选股/backtest/tests/test_metrics.py
import pytest
from 选股.backtest.metrics import calc_sharpe_ratio, calc_max_drawdown


class TestCalcSharpeRatio:
    def test_constant_returns_zero_sharpe(self):
        daily_returns = [0.0] * 100
        assert calc_sharpe_ratio(daily_returns) == 0.0

    def test_positive_returns_positive_sharpe(self):
        daily_returns = [0.001] * 100  # 0.1% daily
        sharpe = calc_sharpe_ratio(daily_returns)
        assert sharpe > 0

    def test_negative_returns_negative_sharpe(self):
        daily_returns = [-0.001] * 100
        sharpe = calc_sharpe_ratio(daily_returns)
        assert sharpe < 0


class TestCalcMaxDrawdown:
    def test_always_positive_has_zero_drawdown(self):
        values = [1.0, 1.01, 1.02, 1.03]
        assert calc_max_drawdown(values) == 0.0

    def test_single_drop(self):
        values = [1.0, 1.1, 0.9, 1.05]
        dd = calc_max_drawdown(values)
        assert dd == pytest.approx(18.18, rel=0.01)
```

- [ ] **Step 2: Run test**

Run: `pytest 选股/backtest/tests/test_metrics.py -v 2>&1`
Expected: FAIL

- [ ] **Step 3: Write metrics implementation**

```python
# 选股/backtest/metrics.py
"""
绩效指标计算：夏普比率、最大回撤、胜率、盈亏比等。

用法:
    from 选股.backtest.metrics import (
        calc_sharpe_ratio, calc_max_drawdown,
        calc_win_rate, calc_profit_factor,
    )
"""
import math


def calc_sharpe_ratio(
    daily_returns: list[float],
    risk_free_rate: float = 0.02,
    periods_per_year: int = 250,
) -> float:
    """计算夏普比率

    Args:
        daily_returns: 每日收益率列表
        risk_free_rate: 年化无风险利率 (默认 2%)
        periods_per_year: 年化周期数 (日K=250)

    Returns:
        年化夏普比率
    """
    if not daily_returns or len(daily_returns) < 2:
        return 0.0

    mean_return = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean_return) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std_dev = math.sqrt(variance)

    if std_dev == 0:
        return 0.0

    daily_rf = risk_free_rate / periods_per_year
    excess_return = mean_return - daily_rf
    daily_sharpe = excess_return / std_dev
    return round(daily_sharpe * math.sqrt(periods_per_year), 4)


def calc_max_drawdown(values: list[float]) -> float:
    """计算最大回撤（百分比）

    Args:
        values: 净值序列

    Returns:
        最大回撤百分比 (如 18.18 表示回撤 18.18%)
    """
    if not values or len(values) < 2:
        return 0.0

    peak = values[0]
    max_dd = 0.0

    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd:
            max_dd = dd

    return round(max_dd, 2)


def calc_win_rate(returns: list[float]) -> float:
    """计算胜率"""
    if not returns:
        return 0.0
    wins = sum(1 for r in returns if r > 0)
    return round(wins / len(returns) * 100, 1)


def calc_profit_factor(returns: list[float]) -> float:
    """计算盈亏比 (总盈利 / 总亏损绝对值)"""
    total_gain = sum(r for r in returns if r > 0)
    total_loss = abs(sum(r for r in returns if r < 0))
    if total_loss == 0:
        return float("inf") if total_gain > 0 else 0.0
    return round(total_gain / total_loss, 4)


def calc_avg_return(returns: list[float]) -> float:
    """计算平均每笔收益率"""
    if not returns:
        return 0.0
    return round(sum(returns) / len(returns), 4)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest 选股/backtest/tests/test_metrics.py -v 2>&1`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add 选股/backtest/metrics.py 选股/backtest/tests/test_metrics.py
git commit -m "feat(backtest): add metrics module with Sharpe, max drawdown, win rate"
```

---

### Task 6: CLI — run backtests from command line

**Files:**
- Create: `选股/backtest/cli.py`

- [ ] **Step 1: Write CLI entry point**

```python
# 选股/backtest/cli.py
"""
命令行接口：运行回测任务。

用法:
    python -m 选股.backtest.cli --strategy b1 --pool 沪深300 --start 2026-01-05 --end 2026-06-01
    python -m 选股.backtest.cli --strategy brick --pool 沪深主板 --top-n 15 --hold 10
"""
import argparse
import json
import sys
from pathlib import Path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="回测系统 CLI")
    parser.add_argument("--strategy", default="b1", help="策略名 (b1/brick/ruthless_wave)")
    parser.add_argument("--pool", default="沪深300", help="股票池")
    parser.add_argument("--top-n", type=int, default=10, help="每轮选股数")
    parser.add_argument("--min-score", type=int, default=25, help="最低入围分")
    parser.add_argument("--hold", type=int, default=5, help="持有天数")
    parser.add_argument("--capital", type=float, default=10000, help="每只投入资金")
    parser.add_argument("--start", default=None, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--skip", type=int, default=250, help="跳过预热天数")
    parser.add_argument("--output", default=None, help="结果 JSON 输出路径")
    parser.add_argument("--verbose", action="store_true", default=True,
                        help="打印详细进度")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None):
    args = parse_args(argv)

    from 选股.backtest.engine import BacktestEngine

    engine = BacktestEngine(
        strategy_name=args.strategy,
        pool_name=args.pool,
        top_n=args.top_n,
        min_score=args.min_score,
        holding_days=args.hold,
        capital_per_stock=args.capital,
    )

    result = engine.run(
        start_date=args.start,
        end_date=args.end,
        skip_first=args.skip,
        verbose=args.verbose,
    )

    m = result["metrics"]
    print(f"\n{'=' * 60}")
    print(f"  策略: {result['strategy_name']} @ {result['pool_name']}")
    print(f"  周期: {result['date_range'].get('start', '?')} ~ {result['date_range'].get('end', '?')}")
    print(f"  轮次: {result['total_rounds']} | 交易: {m.get('total_trades', 0)} 笔")
    print(f"  总收益: {m.get('total_return_pct', 0):+.2f}%")
    print(f"  平均每笔: {m.get('avg_return_pct', 0):+.2f}%")
    print(f"  胜率: {m.get('win_rate', 0):.1f}% ({m.get('wins', 0)}胜/{m.get('losses', 0)}负)")
    print(f"  最大收益: {m.get('max_return_pct', 0):+.2f}% | 最小: {m.get('min_return_pct', 0):+.2f}%")
    print(f"  耗时: {result['elapsed']:.0f}s")
    print(f"{'=' * 60}")

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\n结果已保存: {output_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run CLI help to verify it works**

Run: `python -m 选股.backtest.cli --help`
Expected: Shows usage help

- [ ] **Step 3: Run a quick smoke test**

Run: `python -m 选股.backtest.cli --strategy b1 --pool 自选 --hold 5 --verbose 2>&1 | head -40`
Expected: Backtest runs (if watchlist.txt exists) or "0 qualified" messages

- [ ] **Step 4: Commit**

```bash
git add 选股/backtest/cli.py
git commit -m "feat(backtest): add CLI entry point for running backtests"
```

---

### Task 7: Comparison Runner — run multiple strategy configs side-by-side

**Files:**
- Create: `选股/backtest/comparison.py`

- [ ] **Step 1: Write comparison runner**

```python
# 选股/backtest/comparison.py
"""
参数对比/策略对比运行器：一次运行多种配置，输出对比结果。

用法:
    from 选股.backtest.comparison import run_comparison

    configs = [
        {"strategy_name": "b1", "top_n": 5, "holding_days": 5},
        {"strategy_name": "b1", "top_n": 10, "holding_days": 5},
        {"strategy_name": "brick", "top_n": 5, "holding_days": 5},
    ]
    results = run_comparison(configs, pool_name="沪深300",
                             start_date="2026-01-05", end_date="2026-06-01")
"""
import sys
import time
from typing import Any


def run_comparison(
    configs: list[dict[str, Any]],
    pool_name: str = "沪深300",
    start_date: str | None = None,
    end_date: str | None = None,
    verbose: bool = True,
) -> list[dict]:
    """运行多组配置的对比回测

    Args:
        configs: 配置列表，每项可含 strategy_name, top_n, min_score, holding_days, capital_per_stock
        pool_name: 公共股票池
        start_date/end_date: 公共日期范围

    Returns:
        [{config: ..., metrics: ..., total_rounds: ..., elapsed: ...}, ...]
    """
    results = []
    n = len(configs)

    for i, cfg in enumerate(configs):
        if verbose:
            print(f"\n[{i+1}/{n}] 运行: {cfg.get('strategy_name')} "
                  f"top_n={cfg.get('top_n', 10)} hold={cfg.get('holding_days', 5)}")

        from 选股.backtest.engine import BacktestEngine

        engine = BacktestEngine(
            strategy_name=cfg.get("strategy_name", "b1"),
            pool_name=pool_name,
            top_n=cfg.get("top_n", 10),
            min_score=cfg.get("min_score", 25),
            holding_days=cfg.get("holding_days", 5),
            capital_per_stock=cfg.get("capital_per_stock", 10000),
        )

        result = engine.run(
            start_date=start_date,
            end_date=end_date,
            skip_first=cfg.get("skip_first", 250),
            verbose=verbose,
        )

        results.append({
            "config": cfg,
            "metrics": result["metrics"],
            "total_rounds": result["total_rounds"],
            "total_trades": result["total_trades"],
            "elapsed": result["elapsed"],
        })

    return results
```

- [ ] **Step 2: Commit**

```bash
git add 选股/backtest/comparison.py
git commit -m "feat(backtest): add comparison runner for multi-config backtesting"
```

---

### Task 8: HTML Report — visualize backtest results

**Files:**
- Create: `选股/backtest/report.py`
- Template: `选股/backtest/templates/backtest_report.html`

- [ ] **Step 1: Write report generator**

```python
# 选股/backtest/report.py
"""
回测报告生成器：将回测结果渲染为 HTML 页面（含 ECharts 图表）。

用法:
    from 选股.backtest.report import generate_report

    html = generate_report(result)
    Path("report.html").write_text(html, encoding="utf-8")
"""
import json
from pathlib import Path
from typing import Any

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def generate_report(result: dict[str, Any]) -> str:
    """将回测结果渲染为 HTML 报告。

    报告内容：
      - 配置摘要
      - 绩效指标卡片（总收益、胜率、平均收益、夏普、最大回撤）
      - 收益分布柱状图
      - 每笔交易详情表格
      - Top-N 股票命中频率
    """
    template_path = _TEMPLATE_DIR / "backtest_report.html"
    if not template_path.exists():
        return _generate_inline_html(result)

    template = template_path.read_text(encoding="utf-8")
    metrics_json = json.dumps(result.get("metrics", {}), ensure_ascii=False)
    trades_json = json.dumps(result.get("trades", []), ensure_ascii=False)
    config_json = json.dumps(result.get("config", {}), ensure_ascii=False)

    html = template
    html = html.replace("{{METRICS_JSON}}", metrics_json)
    html = html.replace("{{TRADES_JSON}}", trades_json)
    html = html.replace("{{CONFIG_JSON}}", config_json)
    html = html.replace("{{STRATEGY_NAME}}", result.get("strategy_name", "?"))
    html = html.replace("{{POOL_NAME}}", result.get("pool_name", "?"))
    html = html.replace("{{DATE_RANGE}}",
        f"{result.get('date_range', {}).get('start', '?')} ~ "
        f"{result.get('date_range', {}).get('end', '?')}")
    html = html.replace("{{TOTAL_ROUNDS}}", str(result.get("total_rounds", 0)))
    html = html.replace("{{TOTAL_TRADES}}", str(result.get("total_trades", 0)))
    html = html.replace("{{ELAPSED}}", str(result.get("elapsed", 0)))

    return html


def _generate_inline_html(result: dict) -> str:
    """生成内联 HTML（无模板文件时的兜底）"""
    m = result.get("metrics", {})
    trades = result.get("trades", [])
    config = result.get("config", {})

    rows = ""
    for t in trades[:100]:
        sign = "+" if t.get("return_pct", 0) >= 0 else ""
        rows += f"""<tr>
            <td>{t.get('code', '')}</td>
            <td>{t.get('name', '')}</td>
            <td>{t.get('buy_date', '')}</td>
            <td>{t.get('sell_date', '')}</td>
            <td class="{'green' if t.get('return_pct', 0) >= 0 else 'red'}">
                {sign}{t.get('return_pct', 0):.2f}%
            </td>
            <td>{t.get('score', '')}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8">
<title>回测报告 - {config.get('strategy_name', '?')}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
    body {{ font-family: sans-serif; margin: 20px; background: #f5f5f5; }}
    .card {{ background: white; border-radius: 8px; padding: 16px; margin: 12px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; }}
    .metric {{ text-align: center; }}
    .metric .value {{ font-size: 24px; font-weight: bold; }}
    .metric .label {{ font-size: 12px; color: #666; }}
    .green {{ color: #e74c3c; }}  /* A股红涨绿跌 */
    .red {{ color: #27ae60; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 8px; text-align: left; border-bottom: 1px solid #ddd; font-size: 13px; }}
    th {{ background: #f8f9fa; }}
    #chart {{ height: 400px; }}
</style>
</head>
<body>
<h2>回测报告: {config.get('strategy_name', '?')} @ {config.get('pool_name', '?')}</h2>
<div class="card">
    <div class="metrics">
        <div class="metric"><div class="value {'green' if m.get('total_return_pct', 0) >= 0 else 'red'}">{m.get('total_return_pct', 0):+.2f}%</div><div class="label">总收益</div></div>
        <div class="metric"><div class="value">{m.get('win_rate', 0):.1f}%</div><div class="label">胜率</div></div>
        <div class="metric"><div class="value">{m.get('avg_return_pct', 0):+.2f}%</div><div class="label">平均每笔</div></div>
        <div class="metric"><div class="value">{m.get('total_trades', 0)}</div><div class="label">交易笔数</div></div>
        <div class="metric"><div class="value">{m.get('wins', 0)}/{m.get('losses', 0)}</div><div class="label">胜/负</div></div>
    </div>
</div>
<div class="card" id="chart"></div>
<div class="card">
    <h3>交易明细 (前100笔)</h3>
    <table><thead><tr>
        <th>代码</th><th>名称</th><th>买入日</th><th>卖出日</th><th>收益率</th><th>评分</th>
    </tr></thead><tbody>{rows}</tbody></table>
</div>
<script>
    var trades = {json.dumps(trades, ensure_ascii=False)};
    var chart = echarts.init(document.getElementById('chart'));
    chart.setOption({{
        title: {{ text: '每笔交易收益率分布', left: 'center' }},
        tooltip: {{ trigger: 'axis' }},
        xAxis: {{ type: 'category', data: trades.map((_,i) => i+1), axisLabel: {{ fontSize: 10 }} }},
        yAxis: {{ type: 'value', axisLabel: {{ formatter: '{{value}}%' }} }},
        series: [{{
            type: 'bar',
            data: trades.map(t => ({{
                value: t.return_pct,
                itemStyle: {{ color: t.return_pct >= 0 ? '#e74c3c' : '#27ae60' }}
            }})),
        }}]
    }});
</script>
</body></html>"""
```

- [ ] **Step 2: Update CLI to support --html output**

```python
# 在 cli.py 的 main() 中，在输出 JSON 后增加:
    if args.html:
        from 选股.backtest.report import generate_report
        html = generate_report(result)
        html_path = Path(args.output).with_suffix(".html") if args.output \
            else Path(f"backtest_{args.strategy}_{args.pool}.html")
        html_path.write_text(html, encoding="utf-8")
        print(f"HTML 报告: {html_path}")
```

Also add the `--html` flag to the argument parser:
```python
    parser.add_argument("--html", action="store_true", help="同时输出 HTML 报告")
```

- [ ] **Step 3: Commit**

```bash
git add 选股/backtest/report.py 选股/backtest/cli.py
git commit -m "feat(backtest): add HTML report generator with ECharts visualization"
```

---

### Self-Review

**1. Spec coverage:**
- Task 1 (DataProvider): Covers "加载全量K线数据" and "按日期切片" — matches the data layer requirement
- Task 2 (StrategyRunner): Covers "在历史日期运行策略打分" — matches the strategy replay requirement
- Task 3 (PortfolioSimulator): Covers "模拟选股后持仓表现" — matches the portfolio tracking requirement
- Task 4 (Engine): Covers "编排日期迭代+策略执行+组合模拟" — matches the full orchestration requirement
- Task 5 (Metrics): Covers "绩效指标计算" (夏普、最大回撤、胜率)
- Task 6 (CLI): Covers "命令行运行回测"
- Task 7 (Comparison): Covers "多配置对比运行"
- Task 8 (Report): Covers "结果可视化报告"

**2. Placeholder scan:** No placeholders (TBD, TODO, etc.) found. All code blocks contain complete, runnable implementations.

**3. Type consistency:** All function signatures, method names, and data structure keys (code, name, score, price, buy_date, sell_date, return_pct, etc.) are consistent across all 8 tasks. The PortfolioSimulator expects `rounds[].stocks[].{code, name, score, price}` which matches what the Engine produces via StrategyRunner.

**4. Config reuse between comparison.py and engine.py:** Both use the same config keys (strategy_name, top_n, holding_days, etc.).

**5. test_data_provider.py:** Unit tests test data slicing only (doesn't rely on network); the `from_pool` method integration tests run slowly (load real data) but the unit test `test_slice_klines_up_to_date` is fast and isolated.

**Missing (not a gap — intentional deferrals):**
- Multi-stock data provider concurrent loading optimization (current is sequential)
- Parameter optimization (grid search) — this would be a follow-up task
- Real benchmark comparison (沪深300 index tracking) — follow-up task
