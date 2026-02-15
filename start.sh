#!/bin/bash
# ============================================
# LinuxDo 自动签到 一键部署脚本 (Ubuntu)
# ============================================
# 功能:
#   1. 自动创建 venv + 安装依赖
#   2. 设置 cron: 每天 9:00 北京时间运行 main.py
#   3. 启动 pick_bottle_daemon.py 后台守护进程
# ============================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
PYTHON="$VENV_DIR/bin/python"
PID_FILE="$SCRIPT_DIR/pick_daemon.pid"
LOG_DIR="$SCRIPT_DIR/logs"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err() { echo -e "${RED}[✗]${NC} $1"; }

# ---------- 检查 .env ----------
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    err ".env 文件不存在！请先创建:"
    echo "    cp $SCRIPT_DIR/.env.example $SCRIPT_DIR/.env"
    echo "    然后填入你的账号密码和 TG token"
    exit 1
fi
log ".env 文件已就绪"

# ---------- 创建日志目录 ----------
mkdir -p "$LOG_DIR"

# ---------- 创建 venv ----------
if [ ! -d "$VENV_DIR" ]; then
    log "创建 Python 虚拟环境..."
    python3 -m venv "$VENV_DIR"
fi
log "虚拟环境: $VENV_DIR"

# ---------- 安装依赖 ----------
log "安装/更新依赖..."
"$VENV_DIR/bin/pip" install -q --upgrade pip
"$VENV_DIR/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"
log "依赖安装完成"

# ---------- 设置 cron (每天 01:00 UTC = 09:00 北京时间) ----------
CRON_CMD="0 1 * * * cd $SCRIPT_DIR && $PYTHON main.py >> $LOG_DIR/main.log 2>&1"
CRON_TAG="# linuxdo-daily-main"

# 先移除旧的同标签 cron
(crontab -l 2>/dev/null | grep -v "$CRON_TAG") | crontab - 2>/dev/null || true
# 添加新 cron
(crontab -l 2>/dev/null; echo "$CRON_CMD $CRON_TAG") | crontab -
log "Cron 已设置: 每天 09:00 (北京时间) 运行 main.py"
echo "    日志: $LOG_DIR/main.log"

# ---------- 启动捡瓶子守护进程 ----------
# 先停掉旧进程
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        warn "停止旧的捡瓶子守护进程 (PID: $OLD_PID)..."
        kill "$OLD_PID" 2>/dev/null || true
        sleep 1
    fi
    rm -f "$PID_FILE"
fi

log "启动捡瓶子守护进程..."
nohup "$PYTHON" "$SCRIPT_DIR/pick_bottle_daemon.py" >> "$LOG_DIR/pick_daemon.log" 2>&1 &
DAEMON_PID=$!
echo "$DAEMON_PID" > "$PID_FILE"
log "捡瓶子守护进程已启动 (PID: $DAEMON_PID)"
echo "    日志: $LOG_DIR/pick_daemon.log"

# ---------- 设置开机自启 (cron @reboot) ----------
REBOOT_CMD="@reboot cd $SCRIPT_DIR && $PYTHON pick_bottle_daemon.py >> $LOG_DIR/pick_daemon.log 2>&1 &"
REBOOT_TAG="# linuxdo-pick-daemon"

(crontab -l 2>/dev/null | grep -v "$REBOOT_TAG") | crontab - 2>/dev/null || true
(crontab -l 2>/dev/null; echo "$REBOOT_CMD $REBOOT_TAG") | crontab -
log "开机自启已设置: pick_bottle_daemon.py"

# ---------- 立即运行一次 main.py? ----------
echo ""
echo "=========================================="
echo -e " ${GREEN}部署完成！${NC}"
echo "=========================================="
echo ""
echo "  定时任务:  每天 09:00 (北京) 自动签到"
echo "  捡瓶子:   后台运行中 (8:00-23:00 每10分钟)"
echo ""
echo "  查看签到日志:  tail -f $LOG_DIR/main.log"
echo "  查看捡瓶子日志: tail -f $LOG_DIR/pick_daemon.log"
echo "  停止捡瓶子:    kill \$(cat $PID_FILE)"
echo ""
read -p "  是否立即运行一次签到? [y/N] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    log "开始运行签到..."
    cd "$SCRIPT_DIR" && "$PYTHON" main.py 2>&1 | tee "$LOG_DIR/main.log"
fi