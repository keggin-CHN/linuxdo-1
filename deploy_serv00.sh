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

# 安装依赖
echo "📦 安装依赖..."
"$VENV_DIR/bin/pip" install --upgrade pip -q 2>/dev/null
"$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements-serv00.txt" -q
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

# 设置 cron
CRON_CMD="0 1 * * * $SCRIPT_DIR/run.sh"
(crontab -l 2>/dev/null | grep -v "run.sh"; echo "$CRON_CMD") | crontab -
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