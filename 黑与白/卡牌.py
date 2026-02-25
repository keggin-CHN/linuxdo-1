"""黑与白 - 卡牌每日任务"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import init, log, BASE_URL, delay  # noqa: E402
from utils import send_telegram  # noqa: E402


def run():
    """执行卡牌任务，返回 (成功?, 消息)"""
    session, proxy, user = init()
    username = user.get("name", "未知")

    r = session.get(f"{BASE_URL}/api/cards/draw/status", proxy=proxy, timeout=15)
    log.info(f"抽卡状态接口: HTTP {r.status_code}, body={r.text[:300]}")
    try:
        data = r.json()
    except Exception as e:
        return False, f"🃏 黑与白卡牌\n❌ 获取抽卡状态失败 (解析错误: {e})\nHTTP {r.status_code}\n用户: {username}"
    if not data.get("success"):
        err = data.get("error") or data.get("message") or str(data)[:200]
        return False, f"🃏 黑与白卡牌\n❌ 获取抽卡状态失败\n原因: {err}\n用户: {username}"

    limits = data.get("limits", {})
    free_remaining = limits.get("freeRemaining", 0)
    total_used = limits.get("totalUsed", 0)

    if free_remaining <= 0:
        return True, f"🃏 黑与白卡牌\n✅ 今日免费抽卡已用完\n用户: {username}\n已用: {total_used}次"

    results = []
    draws_done = 0

    while free_remaining > 0:
        if free_remaining >= 10:
            draw_type = "ten"
            count = 10
        else:
            draw_type = "single"
            count = 1

        delay(2, 4)
        log.info(f"抽卡: {draw_type} (免费剩余{free_remaining})")
        r = session.post(
            f"{BASE_URL}/api/cards/draw",
            json={"type": draw_type},
            proxy=proxy, timeout=15,
        )

        if not r.text.strip():
            results.append(f"  ❌ 空响应 (HTTP {r.status_code})")
            break

        try:
            data = r.json()
        except Exception:
            results.append(f"  ❌ 解析失败: {r.text[:100]}")
            break

        if data.get("success"):
            cards = data.get("cards", [])
            for card in cards:
                name = card.get("name", "未知")
                rarity = card.get("rarity", "?")
                results.append(f"  {name} ({rarity})")
            draws_done += len(cards)
            achievements = data.get("grantedAchievements", [])
            if achievements:
                for a in achievements:
                    results.append(f"  🏆 成就: {a.get('name', a)}")
        else:
            error = data.get("error", str(data))
            results.append(f"  ❌ 失败: {error}")
            break

        free_remaining -= count
        if free_remaining > 0:
            time.sleep(2)

    results_text = "\n".join(results) if results else "无"
    msg = (f"🃏 黑与白卡牌\n"
           f"用户: {username}\n"
           f"本次抽取 {draws_done} 张:\n"
           f"{results_text}")
    return True, msg


def main():
    from utils import load_env
    load_env()
    ok, msg = run()
    log.info(msg)
    send_telegram(msg)


if __name__ == "__main__":
    main()