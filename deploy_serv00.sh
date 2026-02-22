#!/bin/bash
# Serv00 一键部署脚本
# SSH 到 serv00 后执行: bash deploy_serv00.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

# ===== 账号配置（写死） =====
export CDK_USERNAME="zhou239289001@gmail.com"
export CDK_PASSWORD="zhou060423rls"
export TG_BOT_TOKEN="7483346980:AAHT4LBRiDU0H617sRQZmNUL8A6GumybMHE"
export TG_CHAT_ID="7420206850"

echo "=== LinuxDo 每日任务 - Serv00 部署 ==="

# ===== 清理历史实例（确保服务器只有一个 linuxdo main.py 任务）=====
echo "🧹 清理历史 linuxdo 实例..."

# 1) 清理旧进程：run.sh / main.py
if command -v pgrep &>/dev/null; then
    OLD_PIDS="$(pgrep -u "$USER" -f 'linuxdo/.*/run\.sh|linuxdo/.*/main\.py|linuxdo/run\.sh|linuxdo/main\.py' || true)"
else
    OLD_PIDS="$(ps -u "$USER" -o pid= -o args= | awk '/linuxdo\/.*(run\.sh|main\.py)|linuxdo\/(run\.sh|main\.py)/{print $1}' || true)"
fi

if [ -n "$OLD_PIDS" ]; then
    echo "发现旧进程: $OLD_PIDS"
    echo "$OLD_PIDS" | xargs -r kill || true
    sleep 2

    if command -v pgrep &>/dev/null; then
        LEFT_PIDS="$(pgrep -u "$USER" -f 'linuxdo/.*/run\.sh|linuxdo/.*/main\.py|linuxdo/run\.sh|linuxdo/main\.py' || true)"
    else
        LEFT_PIDS="$(ps -u "$USER" -o pid= -o args= | awk '/linuxdo\/.*(run\.sh|main\.py)|linuxdo\/(run\.sh|main\.py)/{print $1}' || true)"
    fi

    if [ -n "$LEFT_PIDS" ]; then
        echo "强制结束残留进程: $LEFT_PIDS"
        echo "$LEFT_PIDS" | xargs -r kill -9 || true
    fi
    echo "✅ 旧进程清理完成"
else
    echo "ℹ️ 未发现旧进程"
fi

# 2) 清理旧 cron：所有指向 linuxdo 的 run.sh / main.py 任务
TMP_CRON="$(mktemp)"
(crontab -l 2>/dev/null || true) \
    | grep -Ev 'linuxdo/.*/run\.sh|linuxdo/.*/main\.py|linuxdo/run\.sh|linuxdo/main\.py' \
    > "$TMP_CRON" || true
crontab "$TMP_CRON"
rm -f "$TMP_CRON"
echo "✅ 旧 cron 任务清理完成"

# 检查 Python3
PYTHON=""
for p in python3.11 python3.10 python3.9 python3; do
    if command -v "$p" &>/dev/null; then
        PYTHON="$p"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "❌ 未找到 Python3"
    exit 1
fi
echo "✅ Python: $PYTHON ($($PYTHON --version))"

# 创建虚拟环境
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 创建虚拟环境..."
    $PYTHON -m venv "$VENV_DIR"
fi

# 安装依赖（显示错误，避免静默退出）
echo "📦 安装依赖..."
if [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "❌ 虚拟环境 Python 不存在: $VENV_DIR/bin/python"
    exit 1
fi

# 某些环境 venv 可能没有 pip，先尝试补齐
"$VENV_DIR/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 || true

# 使用 python -m pip 更稳健，并保留输出便于排错
"$VENV_DIR/bin/python" -m pip install --upgrade pip

REQ_FILE="$SCRIPT_DIR/requirements-serv00.txt"
if [ ! -f "$REQ_FILE" ]; then
    REQ_FILE="$SCRIPT_DIR/requirements.txt"
fi
"$VENV_DIR/bin/python" -m pip install -r "$REQ_FILE"
echo "✅ 依赖安装完成"

# 写入 .env 文件
cat > "$SCRIPT_DIR/.env" << 'EOF'
CDK_USERNAME=zhou239289001@gmail.com
CDK_PASSWORD=zhou060423rls
TG_BOT_TOKEN=7483346980:AAHT4LBRiDU0H617sRQZmNUL8A6GumybMHE
TG_CHAT_ID=7420206850
EOF
echo "✅ .env 已写入"

# 创建运行脚本
cat > "$SCRIPT_DIR/run.sh" << RUNEOF
#!/bin/bash
cd "$SCRIPT_DIR"
export CDK_USERNAME="zhou239289001@gmail.com"
export CDK_PASSWORD="zhou060423rls"
export TG_BOT_TOKEN="7483346980:AAHT4LBRiDU0H617sRQZmNUL8A6GumybMHE"
export TG_CHAT_ID="7420206850"
"$VENV_DIR/bin/python" main.py 2>&1 | tee -a "$SCRIPT_DIR/cron.log"
RUNEOF
chmod +x "$SCRIPT_DIR/run.sh"
echo "✅ run.sh 已创建"

# 设置 cron（仅保留一条当前项目任务）
CRON_CMD="0 1 * * * $SCRIPT_DIR/run.sh"
( (crontab -l 2>/dev/null || true); echo "$CRON_CMD" ) | awk 'NF && !seen[$0]++' | crontab -
echo "✅ Cron 已设置: 每天 UTC 01:00 (北京时间 09:00)"

# 测试
echo ""
echo "🧪 测试环境..."
cd "$SCRIPT_DIR"
"$VENV_DIR/bin/python" -c "from utils import load_env; load_env(); print('✅ 环境变量加载成功')"

echo ""
echo "=== 部署完成 ==="
echo "手动运行: $SCRIPT_DIR/run.sh"
echo "查看日志: tail -f $SCRIPT_DIR/cron.log"