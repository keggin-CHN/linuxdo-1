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
- 默认定时为：每天 01:00（系统时区）仅触发一次，并在 01:00-09:00 随机执行一次
- Shop 已接入多站点签到，并按“每站执行后立即推送 TG”
- B4u 任务已从主流程移除
```

## NodeLoc 订阅自动获取与 TG 推送

已新增脚本：`nodeloc/subscription_push.py`

功能：
- 协议登录 NodeLoc（不使用 Selenium/Playwright）
- 走 `https://vip.vip.sd/auth/nodeloc` OAuth 流程
- 从首页提取 `id="sub-link"` 的最新地址（例如 `https://vip.vip.sd/sub/xxxx-uuid`）
- 推送到 Telegram，并将上次结果保存到 `nodeloc/vip_subscription_state.json`

运行：

```bash
python nodeloc/subscription_push.py
```

建议定时（保证在每日 03:00 UUID 轮换后抓取）：

```cron
10 3 * * * cd /path/to/linuxdo && /path/to/linuxdo/venv/bin/python nodeloc/subscription_push.py >> logs/subscription_push.log 2>&1
```

相关环境变量：
- `NODELOC_USERNAME`
- `NODELOC_PASSWORD`
- `VIP_PUSH_ONLY_ON_CHANGE`（1=仅变化推送，0=每次推送）
- `TG_BOT_TOKEN`
- `TG_CHAT_ID`
