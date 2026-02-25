"""黑与白 - 转盘每日任务"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import init, log, BASE_URL, fmt_quota, delay  # noqa: E402
from utils import send_telegram  # noqa: E402


def run():
    """执行转盘任务，返回 (成功?, 消息)"""
    session, proxy, user = init()
    username = user.get("name", "未知")

    r = session.get(f"{BASE_URL}/api/wheel", proxy=proxy, timeout=15)
    log.info(f"转盘状态接口: HTTP {r.status_code}, body={r.text[:300]}")
    try:
        data = r.json()
    except Exception as e:
        msg = f"🎰 黑与白转盘\n❌ 获取转盘信息失败 (解析错误: {e})\nHTTP {r.status_code}\n用户: {username}"
        return False, msg
    if not data.get("success"):
        err = data.get("error") or data.get("message") or str(data)[:200]
        msg = f"🎰 黑与白转盘\n❌ 获取转盘信息失败\n原因: {err}\n用户: {username}"
        return False, msg

    info = data["data"]
    remaining = info.get("remainingSpins", 0)

    if remaining <= 0:
        msg = f"🎰 黑与白转盘\n✅ 今日转盘已用完\n用户: {username}"
        return True, msg

    results = []
    total_amount = 0

    for i in range(remaining):
        delay(2, 4)
        log.info(f"转盘第 {i + 1}/{remaining} 次...")
        r = session.post(f"{BASE_URL}/api/wheel", proxy=proxy, timeout=15)
        data = r.json()

        if r.status_code == 200 and data.get("data"):
            d = data["data"]
            prize = d.get("prize", {})
            name = prize.get("name", "未知")
            amount = prize.get("amount", 0)
            total_amount += amount
            results.append(f"  第{i+1}次: {name} ({fmt_quota(amount)})")
        else:
            error = data.get("error", str(data))
            results.append(f"  第{i+1}次: 失败 ({error})")
            break

        if i < remaining - 1:
            time.sleep(2)

    results_text = "\n".join(results)
    msg = (f"🎰 黑与白转盘\n"
           f"用户: {username}\n"
           f"本次转盘 {len(results)} 次:\n"
           f"{results_text}\n"
           f"总计获得: {fmt_quota(total_amount)}")
    return True, msg


def main():
    from utils import load_env
    load_env()
    ok, msg = run()
    log.info(msg)
    send_telegram(msg)


if __name__ == "__main__":
    main()