```
git clone https://github.com/keggin-CHN/linuxdo.git
cd linuxdo
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

```
cat > .env << 'EOF'
CDK_USERNAME=zhou239289001@gmail.com
CDK_PASSWORD=zhou060423rls
TG_BOT_TOKEN=7483346980:AAHT4LBRiDU0H617sRQZmNUL8A6GumybMHE
TG_CHAT_ID=7420206850
EOF
```

```
chmod +x start.sh stop.sh
bash start.sh
```

```text
说明:
- 每日任务统一由 main.py 调度执行
- Shop 已接入多站点签到，并按“每站执行后立即推送 TG”
- B4u 任务已从主流程移除
```
