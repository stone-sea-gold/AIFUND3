# 波浪理论交易系统

基于 **艾略特波浪理论** + **无情操盘手均线量价系统** 的 A 股技术分析框架。

## 目录结构

```
├── 知识库/              ← 完整交易知识体系
│   ├── 艾略特波浪理论/  ← 公理/铁律/调整浪/MACD/Fibonacci/交易策略
│   └── 无情操盘手/      ← 白黄线系统/买点卖点/砖柱/暴力K线/纪律
├── 策略/
│   └── 回测策略操作手册.md  ← 7只票的回测最优策略条件清单
├── 分析/
│   └── 波浪分析报告.md   ← 波浪划分与情景分析
├── 脚本/                ← 数据获取与回测脚本
├── server/              ← FastAPI Web 看板
├── 数据/                ← K线JSON数据
└── doc/                 ← 原始教材
```

## 核心脚本

- `fetch_wave_data.py` — 抓取K线 (东方财富/新浪)，计算 MACD/均线/布林带/Fibonacci
- `backtest_ruthless.py` — 回测引擎
- `daily_analysis.py` — 收盘自动分析（需 DeepSeek API Key）
- `extract_screenshot.py` — OCR 提取行情截图 MACD 值
- `watch_analysis.py` — 观察池分析

## Web 看板

```bash
uvicorn server.app:app --host 127.0.0.1 --port 8002
```

## 数据来源

东方财富 / 新浪财经（前复权）

---

> 风险提示：本系统仅供学习研究参考，不构成投资建议。
