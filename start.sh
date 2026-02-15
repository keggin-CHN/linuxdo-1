#!/bin/bash
# ============================================
# LinuxDo 自动签到 一键部署脚本 (Ubuntu)
# ============================================
# 功能:
#   1. 自动创建 venv + 安装依赖
#   2. 设置 cron: 每天 9:00 北京时间运行 main.py
# ============================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
PYTHON="$VENV_DIR/bin/python"
LOG_DIR="$SCRIPT_DIR/logs"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

log() { echo -e "${GREEN}[✓]${NC} $1"; }
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

# ---------- 完成 ----------
echo ""
echo "=========================================="
echo -e " ${GREEN}部署完成！${NC}"
echo "=========================================="
echo ""
echo "  定时任务:  每天 09:00 (北京) 自动签到"
echo "  查看日志:  tail -f $LOG_DIR/main.log"
echo ""
read -p "  是否立即运行一次签到? [y/N] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    log "开始运行签到..."
    cd "$SCRIPT_DIR" && "$PYTHON" main.py 2>&1 | tee "$LOG_DIR/main.log"
fi