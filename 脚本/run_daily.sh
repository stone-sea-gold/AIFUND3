#!/bin/bash
# 每日收盘后自动分析，工作日 15:35 运行
LOG=/tmp/wave_daily.log
echo "=== $(date) ===" >> $LOG
docker exec gh-copilot-persist bash -c   'cd /work/WaveformTheory && python3 脚本/daily_analysis.py' >> $LOG 2>&1
echo "done" >> $LOG
