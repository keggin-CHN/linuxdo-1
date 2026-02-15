#!/bin/bash
# 停止 LinuxDo 所有后台服务

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPT_DIR/pick_daemon.pid"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

# 停止捡瓶子守护进程
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo -e "${GREEN}[✓]${NC} 捡瓶子守护进程已停止 (PID: $PID)"
    else
        echo -e "${RED}[✗]${NC} 进程 $PID 已不存在"
    fi
    rm -f "$PID_FILE"
else
    echo -e "${RED}[✗]${NC} 未找到 PID 文件，守护进程可能未运行"
fi

# 移除 cron
MAIN_TAG="# linuxdo-daily-main"
DAEMON_TAG="# linuxdo-pick-daemon"
(crontab -l 2>/dev/null | grep -v "$MAIN_TAG" | grep -v "$DAEMON_TAG") | crontab - 2>/dev/null || true
echo -e "${GREEN}[✓]${NC} Cron 定时任务已移除"

echo ""
echo "所有 LinuxDo 服务已停止。重新启动请运行: bash start.sh"