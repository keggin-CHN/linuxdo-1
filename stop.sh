#!/bin/bash
# 停止 LinuxDo 定时任务

GREEN='\033[0;32m'
NC='\033[0m'

# 移除 cron
MAIN_TAG="# linuxdo-daily-main"
(crontab -l 2>/dev/null | grep -v "$MAIN_TAG") | crontab - 2>/dev/null || true
echo -e "${GREEN}[✓]${NC} Cron 定时任务已移除"

echo ""
echo "LinuxDo 定时任务已停止。重新启动请运行: bash start.sh"