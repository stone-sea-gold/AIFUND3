"""
回测报告生成器：将回测结果渲染为 HTML 页面（含 ECharts 图表）。
"""

from __future__ import annotations

import json
from typing import Any


def generate_report(result: dict[str, Any]) -> str:
    """将回测结果渲染为 HTML 报告。

    Args:
        result: BacktestEngine.run() 返回的结果字典，包含:
            - strategy_name, pool_name, date_range, total_rounds, total_trades
            - metrics: {total_trades, total_return_pct, avg_return_pct, win_rate, wins, losses, ...}
            - trades: [{code, name, buy_date, sell_date, buy_price, sell_price, return_pct, pnl, score}, ...]
            - config: {...}
            - elapsed: float

    Returns:
        HTML 字符串
    """
    strategy_name = result.get("strategy_name", "未知策略")
    pool_name = result.get("pool_name", "未知股票池")
    date_range = result.get("date_range", ("", ""))
    date_start = date_range[0] if isinstance(date_range, (list, tuple)) else ""
    date_end = date_range[1] if isinstance(date_range, (list, tuple)) else ""
    elapsed = result.get("elapsed", 0)
    metrics = result.get("metrics", {})
    trades = result.get("trades", [])
    config = result.get("config", {})
    total_rounds = result.get("total_rounds", 0)

    # 指标值
    total_return_pct = metrics.get("total_return_pct", 0)
    avg_return_pct = metrics.get("avg_return_pct", 0)
    win_rate = metrics.get("win_rate", 0)
    total_trades = metrics.get("total_trades", 0)
    wins = metrics.get("wins", 0)
    losses = metrics.get("losses", 0)

    # 图表数据
    trade_returns = [t.get("return_pct", 0) for t in trades]
    trade_codes = [f"{t.get('code', '')}" for t in trades]

    # 按选股日期聚合的累积收益曲线
    date_map: dict[str, list[float]] = {}
    for t in trades:
        d = t.get("buy_date", "")
        if d:
            date_map.setdefault(d, []).append(t.get("return_pct", 0))
    cum_dates = sorted(date_map.keys())
    daily_avg = [sum(date_map[d]) / len(date_map[d]) for d in cum_dates]
    cumulative = []
    cum = 0.0
    for r in daily_avg:
        cum += r
        cumulative.append(round(cum, 4))

    # 交易明细（最多前100条）
    trades_display = trades[:100]

    # 序列化为 JSON
    trade_returns_json = json.dumps(trade_returns, ensure_ascii=False)
    trade_codes_json = json.dumps(trade_codes, ensure_ascii=False)
    cumulative_json = json.dumps(cumulative, ensure_ascii=False)
    cum_dates_json = json.dumps(cum_dates, ensure_ascii=False)
    trades_json = json.dumps(trades_display, ensure_ascii=False)
    config_json = json.dumps(config, ensure_ascii=False, indent=2)

    # 格式化耗时
    if elapsed >= 60:
        elapsed_str = f"{elapsed / 60:.1f} 分钟"
    else:
        elapsed_str = f"{elapsed:.1f} 秒"

    # 收益率颜色类
    def return_color_class(val: float) -> str:
        if val > 0:
            return "up"
        elif val < 0:
            return "down"
        return ""

    def format_pct(val: float) -> str:
        return f"{val:+.2f}%"

    # 构建交易表格行
    trade_rows = []
    for t in trades_display:
        ret_pct = t.get("return_pct", 0)
        color_cls = return_color_class(ret_pct)
        trade_rows.append(
            f"<tr>"
            f"<td>{t.get('code', '')}</td>"
            f"<td>{t.get('name', '')}</td>"
            f"<td>{t.get('buy_date', '')}</td>"
            f"<td>{t.get('sell_date', '')}</td>"
            f"<td class=\"{color_cls}\">{format_pct(ret_pct)}</td>"
            f"<td>{t.get('score', '')}</td>"
            f"</tr>"
        )
    trade_rows_html = "\n".join(trade_rows)

    # 指标卡片 HTML
    total_ret_cls = return_color_class(total_return_pct)
    avg_ret_cls = return_color_class(avg_return_pct)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>回测报告 - {strategy_name}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: "PingFang SC", "Microsoft YaHei", "Hiragino Sans GB",
                 "Helvetica Neue", Arial, sans-serif;
    background: #f0f2f5;
    color: #333;
    line-height: 1.6;
    padding: 20px;
}}
.container {{ max-width: 1200px; margin: 0 auto; }}

/* 标题栏 */
.header {{
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    color: #fff;
    padding: 30px 36px;
    border-radius: 12px;
    margin-bottom: 20px;
    box-shadow: 0 4px 16px rgba(0,0,0,0.15);
}}
.header h1 {{
    font-size: 24px;
    font-weight: 600;
    margin-bottom: 8px;
}}
.header .meta {{
    font-size: 14px;
    color: #a0aec0;
    display: flex;
    gap: 24px;
    flex-wrap: wrap;
}}
.header .meta span {{ display: inline-flex; align-items: center; gap: 4px; }}

/* 指标卡片 */
.metrics-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px;
    margin-bottom: 20px;
}}
.metric-card {{
    background: #fff;
    border-radius: 10px;
    padding: 20px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    text-align: center;
}}
.metric-card .label {{
    font-size: 13px;
    color: #8c8c8c;
    margin-bottom: 8px;
}}
.metric-card .value {{
    font-size: 28px;
    font-weight: 700;
    color: #333;
}}
.metric-card .value.up {{ color: #e74c3c; }}
.metric-card .value.down {{ color: #27ae60; }}

/* 图表区 */
.chart-section {{
    background: #fff;
    border-radius: 10px;
    padding: 24px;
    margin-bottom: 20px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}}
.chart-section h2 {{
    font-size: 16px;
    font-weight: 600;
    margin-bottom: 16px;
    color: #1a1a2e;
}}
.chart-box {{
    width: 100%;
    height: 400px;
}}

/* 表格区 */
.table-section {{
    background: #fff;
    border-radius: 10px;
    padding: 24px;
    margin-bottom: 20px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    overflow-x: auto;
}}
.table-section h2 {{
    font-size: 16px;
    font-weight: 600;
    margin-bottom: 16px;
    color: #1a1a2e;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}}
th, td {{
    padding: 10px 14px;
    text-align: left;
    border-bottom: 1px solid #f0f0f0;
    white-space: nowrap;
}}
th {{
    background: #fafafa;
    font-weight: 600;
    color: #555;
    position: sticky;
    top: 0;
}}
tr:hover {{ background: #fafbfc; }}
td.up {{ color: #e74c3c; font-weight: 600; }}
td.down {{ color: #27ae60; font-weight: 600; }}

/* 配置区 */
.config-section {{
    background: #fff;
    border-radius: 10px;
    padding: 24px;
    margin-bottom: 20px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}}
.config-section h2 {{
    font-size: 16px;
    font-weight: 600;
    margin-bottom: 16px;
    color: #1a1a2e;
}}
.config-section pre {{
    background: #f8f9fa;
    border-radius: 8px;
    padding: 16px;
    font-size: 13px;
    overflow-x: auto;
    font-family: "Cascadia Code", "Fira Code", monospace;
}}

.footer {{
    text-align: center;
    color: #aaa;
    font-size: 12px;
    padding: 20px 0;
}}
</style>
</head>
<body>
<div class="container">

    <!-- 标题栏 -->
    <div class="header">
        <h1>回测报告 — {strategy_name} @ {pool_name}</h1>
        <div class="meta">
            <span>{date_start} ~ {date_end}</span>
            <span>共 {total_rounds} 轮</span>
            <span>耗时 {elapsed_str}</span>
        </div>
    </div>

    <!-- 指标卡片 -->
    <div class="metrics-grid">
        <div class="metric-card">
            <div class="label">总收益率</div>
            <div class="value {total_ret_cls}">{format_pct(total_return_pct)}</div>
        </div>
        <div class="metric-card">
            <div class="label">胜率</div>
            <div class="value">{win_rate:.1f}%</div>
        </div>
        <div class="metric-card">
            <div class="label">平均每笔收益</div>
            <div class="value {avg_ret_cls}">{format_pct(avg_return_pct)}</div>
        </div>
        <div class="metric-card">
            <div class="label">交易笔数</div>
            <div class="value">{total_trades}</div>
        </div>
        <div class="metric-card">
            <div class="label">盈利笔数</div>
            <div class="value up">{wins}</div>
        </div>
        <div class="metric-card">
            <div class="label">亏损笔数</div>
            <div class="value down">{losses}</div>
        </div>
    </div>

    <!-- 收益率分布图 -->
    <div class="chart-section">
        <h2>收益率分布</h2>
        <div id="chart-return-dist" class="chart-box"></div>
    </div>

    <!-- 累积收益曲线 -->
    <div class="chart-section">
        <h2>累积收益曲线</h2>
        <div id="chart-cumulative" class="chart-box"></div>
    </div>

    <!-- 交易明细 -->
    <div class="table-section">
        <h2>交易明细（前 100 笔）</h2>
        <table>
            <thead>
                <tr>
                    <th>代码</th>
                    <th>名称</th>
                    <th>买入日</th>
                    <th>卖出日</th>
                    <th>收益率</th>
                    <th>评分</th>
                </tr>
            </thead>
            <tbody>
                {trade_rows_html}
            </tbody>
        </table>
    </div>

    <!-- 回测配置 -->
    <div class="config-section">
        <h2>回测配置</h2>
        <pre>{config_json}</pre>
    </div>

    <div class="footer">由回测系统自动生成</div>

</div>

<script>
(function() {{
    var returnsData = {trade_returns_json};
    var codesData = {trade_codes_json};
    var cumulativeData = {cumulative_json};
    var cumDates = {cum_dates_json};

    // --- 收益率分布柱状图 ---
    var distChart = echarts.init(document.getElementById('chart-return-dist'));
    distChart.setOption({{
        tooltip: {{
            trigger: 'axis',
            axisPointer: {{ type: 'shadow' }},
            formatter: function(p) {{
                var d = p[0];
                return codesData[d.dataIndex] + '<br/>收益率: ' + d.value.toFixed(2) + '%';
            }}
        }},
        grid: {{ left: 60, right: 30, top: 20, bottom: 60 }},
        xAxis: {{
            type: 'category',
            data: codesData,
            axisLabel: {{
                rotate: 45,
                fontSize: 10,
                interval: function(idx) {{ return returnsData.length <= 30 || idx % Math.ceil(returnsData.length / 30) === 0; }}
            }}
        }},
        yAxis: {{
            type: 'value',
            axisLabel: {{ formatter: '{{value}}%' }},
            splitLine: {{ lineStyle: {{ type: 'dashed', color: '#eee' }} }}
        }},
        series: [{{
            type: 'bar',
            data: returnsData.map(function(v) {{
                return {{
                    value: v,
                    itemStyle: {{ color: v >= 0 ? '#e74c3c' : '#27ae60' }}
                }};
            }}),
            barMaxWidth: 40
        }}]
    }});

    // --- 累积收益曲线 ---
    var cumChart = echarts.init(document.getElementById('chart-cumulative'));
    cumChart.setOption({{
        tooltip: {{
            trigger: 'axis',
            formatter: function(p) {{
                return cumDates[p[0].dataIndex] + '<br/>累积收益: ' + p[0].value.toFixed(2) + '%';
            }}
        }},
        grid: {{ left: 60, right: 30, top: 20, bottom: 40 }},
        xAxis: {{
            type: 'category',
            data: cumDates,
            axisLabel: {{
                fontSize: 10,
                rotate: 45,
                interval: function(idx) {{ return cumDates.length <= 30 || idx % Math.ceil(cumDates.length / 30) === 0; }}
            }},
            name: '日期',
            nameLocation: 'center',
            nameGap: 28
        }},
        yAxis: {{
            type: 'value',
            axisLabel: {{ formatter: '{{value}}%' }},
            splitLine: {{ lineStyle: {{ type: 'dashed', color: '#eee' }} }}
        }},
        series: [{{
            type: 'line',
            data: cumulativeData,
            smooth: true,
            symbol: 'circle',
            symbolSize: 4,
            lineStyle: {{ color: '#e74c3c', width: 2 }},
            itemStyle: {{ color: '#e74c3c' }},
            areaStyle: {{
                color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                    {{ offset: 0, color: 'rgba(231,76,60,0.25)' }},
                    {{ offset: 1, color: 'rgba(231,76,60,0.02)' }}
                ])
            }}
        }}]
    }});

    // 响应式
    window.addEventListener('resize', function() {{
        distChart.resize();
        cumChart.resize();
    }});
}})();
</script>
</body>
</html>"""

    return html
