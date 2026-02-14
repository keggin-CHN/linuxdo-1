# LinuxDo 每日自动任务

基于 LinuxDo OAuth 的多站点每日签到/抽奖自动化工具。

## 支持站点

| 站点 | 功能 | 认证方式 |
|------|------|----------|
| 薄荷 (qd.x666.me) | 签到转盘 | OCID → JWT Bearer |
| 测试1 (openai.api-test.us.ci) | 签到 | new-api OAuth |
| 慕鸢 (newapi.linuxdo.edu.rs) | 签到 | new-api OAuth |
| B4u (tw.b4u.qzz.io) | 抽奖+兑换 | NextAuth OIDC + Server Actions |
| 黑与白 (cdk.hybgzs.com) | 转盘/卡牌/漂流瓶 | CDK OAuth |

## GitHub Actions 自动运行

每天 UTC+8 09:00 自动执行，需要配置以下 Secrets：

| Secret | 说明 |
|--------|------|
| `CDK_USERNAME` | LinuxDo 登录邮箱 |
| `CDK_PASSWORD` | LinuxDo 登录密码 |
| `TG_BOT_TOKEN` | Telegram Bot Token |
| `TG_CHAT_ID` | Telegram Chat ID |

也可以在 Actions 页面手动触发 (workflow_dispatch)。

## 本地运行

```bash
pip install -r requirements.txt

# 创建 .env 文件
cat > .env << EOF
CDK_USERNAME=your_email
CDK_PASSWORD=your_password
TG_BOT_TOKEN=your_bot_token
TG_CHAT_ID=your_chat_id
EOF

python main.py
```

## 运行逻辑

1. CDK 登录获取认证
2. 依次执行：薄荷 → 测试1 → 慕鸢 → B4U → 黑与白(转盘/卡牌/漂流瓶)
3. 任何任务出错则跳过，继续下一个
4. 全部执行完后，重试失败任务（最多 5 次）
5. 汇总所有结果推送到 Telegram