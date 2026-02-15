"""黑与白 - 漂流瓶每日任务"""
import sys
import os
import random
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import init, log, BASE_URL, fmt_quota, delay  # noqa: E402
from utils import send_telegram  # noqa: E402

HITOKOTO_URL = "https://v1.hitokoto.cn"
HITOKOTO_TYPES = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "k", "l"]
PICK_SCOPES = ["friends", "world", "sea", "ocean"]
MAX_PICK_RETRIES = 5


def get_hitokoto(session, proxy):
    t = random.choice(HITOKOTO_TYPES)
    try:
        r = session.get(f"{HITOKOTO_URL}?encode=json&c={t}", proxy=proxy, timeout=10)
        if r.status_code == 200:
            data = r.json()
            text = data.get("hitokoto", "")
            source = data.get("from", "")
            if text:
                return f"{text} ——{source}" if source else text
    except Exception as e:
        log.warning(f"一言获取失败: {e}")
    return "今天也是元气满满的一天，希望每个人都能开开心心！加油鸭~"


def find_duplicate_card(session, proxy):
    delay(2, 3)
    r = session.get(f"{BASE_URL}/api/cards/my", proxy=proxy, timeout=15)
    try:
        data = r.json()
    except Exception:
        return None, None
    if not data.get("success"):
        return None, None
    cards = data.get("cards", [])
    duplicates = [c for c in cards if c.get("quantity", 0) > 1]
    if not duplicates:
        return None, None
    duplicates.sort(key=lambda c: (-c.get("quantity", 0), c.get("rarity", "") == "common"))
    card = duplicates[0]
    log.info(f"找到重复卡牌: {card['name']} ({card['rarity']}) x{card['quantity']}")
    return card["cardId"], card["name"]


def throw_bottle(session, proxy, results):
    card_id, card_name = find_duplicate_card(session, proxy)
    if not card_id:
        results.append("扔瓶子: 跳过（没有重复卡牌）")
        return False

    note = get_hitokoto(session, proxy)
    results.append(f"瓶子文案: {note}")
    results.append(f"附带卡牌: {card_name}")

    delay(2, 4)
    r = session.post(
        f"{BASE_URL}/api/drift-bottle/throw",
        json={"noteContent": note, "cardId": card_id, "isAnonymous": True},
        proxy=proxy, timeout=15,
    )
    try:
        data = r.json()
    except Exception:
        data = {}

    if r.status_code == 200 and data.get("success"):
        results.append("扔瓶子: 成功")
        return True
    else:
        error = data.get("error", f"HTTP {r.status_code}")
        results.append(f"扔瓶子: 失败 ({error})")
        return False


def pick_bottle(session, proxy, results):
    for attempt in range(1, MAX_PICK_RETRIES + 1):
        wait = random.uniform(55, 75)
        if attempt > 1:
            log.info(f"第{attempt}次尝试捡瓶子，等待{wait:.0f}秒...")
            results.append(f"捡瓶子: 第{attempt}次重试，等待{wait:.0f}秒")
            time.sleep(wait)

        delay(2, 4)
        scope = random.choice(PICK_SCOPES)
        r = session.post(
            f"{BASE_URL}/api/drift-bottle/pick",
            json={"scope": scope},
            proxy=proxy, timeout=15,
        )
        try:
            data = r.json()
        except Exception:
            data = {}

        code = data.get("code", "")

        if r.status_code == 200 and data.get("success"):
            d = data.get("data", {})
            bottle = d.get("bottle", {})
            content = bottle.get("noteContent", bottle.get("content", ""))
            quota = bottle.get("quota", 0)
            card = bottle.get("card")
            sender = bottle.get("sender", {}).get("name", "匿名")
            results.append("捡瓶子: 成功")
            results.append(f"发送者: {sender}")
            if content:
                results.append(f"瓶中内容: {content[:100]}")
            if quota:
                results.append(f"瓶中额度: {fmt_quota(quota)}")
            if card:
                results.append(f"瓶中卡牌: {card.get('name', '未知')} ({card.get('rarity', '?')})")
            return True

        if code in ("PICK_COOLDOWN", "INVALID_SCOPE"):
            continue

        error = data.get("error", f"HTTP {r.status_code}")
        results.append(f"捡瓶子: 失败 ({error})")
        return False

    results.append(f"捡瓶子: {MAX_PICK_RETRIES}次重试后仍失败")
    return False


def run():
    """执行漂流瓶任务，返回 (成功?, 消息)"""
    session, proxy, user = init()
    username = user.get("name", "未知")

    r = session.get(f"{BASE_URL}/api/drift-bottle/settings", proxy=proxy, timeout=15)
    data = r.json()
    if r.status_code != 200 or not data.get("success"):
        return False, f"🍾 黑与白漂流瓶\n❌ 获取设置失败\n用户: {username}"

    settings = data["data"]
    enabled = settings.get("settings", {}).get("enabled", False)
    if not enabled:
        return True, f"🍾 黑与白漂流瓶\n⚠️ 功能未开启\n用户: {username}"

    if settings.get("isBlacklisted"):
        return True, f"🍾 黑与白漂流瓶\n⚠️ 已被黑名单\n用户: {username}"

    usage = settings.get("usage", {})
    throw_remaining = usage.get("throwRemaining", 0)
    pick_remaining = usage.get("pickRemaining", 0)
    throw_used = usage.get("throwUsed", 0)
    pick_used = usage.get("pickUsed", 0)
    pick_limit = settings.get("settings", {}).get("dailyPickLimit", 1)

    results = []
    results.append(f"扔瓶子: {throw_used}次已用, 剩余{throw_remaining}次")
    results.append(f"捡瓶子: {pick_used}/{pick_limit}次已用, 剩余{pick_remaining}次")

    if pick_remaining <= 0:
        msg = (f"🍾 黑与白漂流瓶\n✅ 今日已完成\n用户: {username}\n" + "\n".join(results))
        return True, msg

    if throw_used < 1 and throw_remaining > 0:
        throw_bottle(session, proxy, results)

    pick_bottle(session, proxy, results)

    results_text = "\n".join(results)
    msg = f"🍾 黑与白漂流瓶\n用户: {username}\n{results_text}"
    return True, msg


def main():
    from utils import load_env
    load_env()
    ok, msg = run()
    log.info(msg)
    send_telegram(msg)


if __name__ == "__main__":
    main()