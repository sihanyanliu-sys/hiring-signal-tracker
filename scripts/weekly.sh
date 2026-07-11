#!/bin/bash
# 每周采集:管线 -> git 备份(若已配置远程) -> 系统通知
# 由 launchd 调用(~/Library/LaunchAgents/com.hiring-signal-tracker.weekly.plist)
cd "$(dirname "$0")/.." || exit 1
{
  echo "=== $(date '+%F %T') ==="
  /usr/bin/python3 scripts/pipeline.py
  STATUS=$?
  if [ -d .git ]; then
    git add -A data reports
    git commit -q -m "weekly snapshot $(date +%F)" && git push -q 2>&1
  fi
  exit $STATUS
} >> data/weekly.log 2>&1
if [ $? -eq 0 ]; then
  osascript -e 'display notification "本周报告已生成,可打开 reports/ 查看" with title "招聘信号采集完成"' 2>/dev/null
else
  osascript -e 'display notification "采集失败,详见 data/weekly.log" with title "招聘信号采集出错"' 2>/dev/null
fi
