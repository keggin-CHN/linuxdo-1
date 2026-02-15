"""
漂流瓶捡瓶子守护脚本
- 北京时间 8:00-23:00 每 10 分钟尝试捡一次瓶子
- 复用已有 CDK session（不重复登录）
- 捡到后 TG 推送并当天停止
- 第二天 8:00 自动重新开始
"""
import os
import sys
import time
import random
import logging
from datetime import datetime, timezone, timedelta

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, os.path.join(ROOT_DIR, "黑与白"))

from utils import load_env, send_telegram, get_proxy, create_session, delay  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("捡瓶子")

BJ_TZ = timezone(timedelta(hours=8))
BASE_URL = "https://cdk.hybgzs.com"
PICK_SCOPES = ["friends", "world", "sea", "ocean"]
INTERVAL_MINUTES = 10


def get_bj_now():
    return datetime.now(BJ_TZ)


def init_session():
    """初始化 session 并加载 CDK cookies"""
    from common import load_session_cookies, verify_session
    session, imp = create_session()
    proxy = get_proxy()
    log.info(f"浏览器指纹: {imp}")
    load_session_cookies(session)
    user = verify_session(session, proxy)
    return session, proxy, user


def check_pick_remaining(session, proxy):
    """检查今日捡瓶子剩余次数"""
    r = session.get(f"{BASE_URL}/api/drift-bottle/settings", proxy=proxy, timeout=15)
    data = r.json()
    if r.status_code != 200 or not data.get("success"):
        log.warning("获取漂流瓶设置失败")
        return -1  # 未知，继续尝试
    settings = data["data"]
    if not settings.get("settings", {}).get("enabled", False):
        log.info("漂流瓶功能未开启")
        return 0
    usage = settings.get("usage", {})
    return usage.get("pickRemaining", 0)


def try_pick(session, proxy):
    """尝试捡一次瓶子，返回 (成功?, 结果消息)"""
    delay(1, 3)
    scope = random.choice(PICK_SCOPES)
    log.info(f"尝试捡瓶子 (scope={scope})...")
    r = session.post(
        f"{BASE_URL}/api/drift-bottle/pick",
        json={"scope": scope},
        proxy=proxy, timeout=15,
    )
    try:
        data = r.json()
    except Exception:
        return False, f"响应解析失败 (HTTP {r.status_code})"

    code = data.get("code", "")

    if r.status_code == 200 and data.get("success"):
        d = data.get("data", {})
        bottle = d.get("bottle", {})
        content = bottle.get("noteContent", bottle.get("content", ""))
        quota = bottle.get("quota", 0)
        card = bottle.get("card")
        sender = bottle.get("sender", {}).get("name", "匿名")

        lines = ["🍾 捡到漂流瓶!"]
        lines.append(f"发送者: {sender}")
        if content:
            lines.append(f"内容: {content[:200]}")
        if quota:
            lines.append(f"额度: ${quota / 500000:.1f}")
        if card:
            lines.append(f"卡牌: {card.get('name', '未知')} ({card.get('rarity', '?')})")
        return True, "\n".join(lines)

    if code in ("PICK_COOLDOWN", "INVALID_SCOPE"):
        return False, f"冷却中 ({code})"

    error = data.get("error", f"HTTP {r.status_code}")
    return False, f"失败: {error}"


def run_daemon():
    """主循环：8-23点每10分钟捡一次"""
    load_env()
    log.info("漂流瓶捡瓶子守护脚本启动")

    session = None
    proxy = None
    user = None
    last_success_date = None  # 记录上次成功捡到的日期

    while True:
        now = get_bj_now()
        hour = now.hour
        today = now.date()

        # 不在 8-23 点范围内，等到 8 点
        if hour < 8 or hour >= 23:
            next_8am = now.replace(hour=8, minute=0, second=0, microsecond=0)
            if hour >= 23:
                next_8am += timedelta(days=1)
            wait_secs = (next_8am - now).total_seconds()
            log.info(f"当前 {now.strftime('%H:%M')}，不在运行时段，等待到明天 8:00 ({wait_secs/3600:.1f}h)")
            time.sleep(wait_secs + 10)
            continue

        # 今天已经捡到了，等到明天 8 点
        if last_success_date == today:
            next_8am = now.replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
            wait_secs = (next_8am - now).total_seconds()
            log.info(f"今日已捡到瓶子，等待到明天 8:00 ({wait_secs/3600:.1f}h)")
            time.sleep(wait_secs + 10)
            continue

        # 初始化/重新初始化 session
        if session is None:
            try:
                session, proxy, user = init_session()
                log.info(f"已登录: {user.get('name', '未知')}")
            except Exception as e:
                log.error(f"初始化 session 失败: {e}")
                log.info("60 秒后重试...")
                time.sleep(60)
                continue

        # 检查剩余次数
        remaining = check_pick_remaining(session, proxy)
        if remaining == 0:
            log.info("今日捡瓶子次数已用完")
            last_success_date = today
            next_8am = now.replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
            wait_secs = (next_8am - now).total_seconds()
            time.sleep(wait_secs + 10)
            continue

        # 尝试捡瓶子
        try:
            ok, msg = try_pick(session, proxy)
        except Exception as e:
            log.error(f"捡瓶子异常: {e}")
            # session 可能失效，重置
            session = None
            time.sleep(60)
            continue

        if ok:
            log.info(f"捡到了!\n{msg}")
            send_telegram(msg)
            last_success_date = today
            # 等到明天
            next_8am = now.replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
            wait_secs = (next_8am - now).total_seconds()
            log.info(f"今日完成，等待到明天 8:00")
            time.sleep(wait_secs + 10)
        else:
            log.info(f"未捡到: {msg}")
            # 等 10 分钟 ± 随机偏移
            wait = INTERVAL_MINUTES * 60 + random.uniform(-60, 60)
            log.info(f"等待 {wait/60:.1f} 分钟后重试...")
            time.sleep(wait)


if __name__ == "__main__":
    run_daemon()