git clone https://github.com/keggin-CHN/linuxdo.git
cd linuxdo
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

```bash
cat > .env << 'EOF'
CDK_USERNAME=zhou239289001@gmail.com
CDK_PASSWORD=zhou060423rls
TG_BOT_TOKEN=7483346980:AAHT4LBRiDU0H617sRQZmNUL8A6GumybMHE
TG_CHAT_ID=7420206850
EOF
```

```bash
chmod +x start.sh stop.sh
bash start.sh
```

## GitHub 更新旧版本（先停旧服务和 cron）

```bash
cd /path/to/linuxdo
bash stop.sh
crontab -l | grep -v 'linuxdo-daily-main' | crontab -
pkill -f "python.*main.py" || true
```

```bash
git fetch --all
git reset --hard origin/main
```

```bash
source venv/bin/activate
pip install -r requirements.txt
bash start.sh
```

```bash
crontab -l | grep linuxdo-daily-main
```

说明：
- 以上更新流程会先停掉旧服务和旧 cron，再拉最新代码并重新部署。
- [`佬友公益站/签到.py`](linuxdo/佬友公益站/签到.py) 已内置 TG 推送配置，可直接使用。
