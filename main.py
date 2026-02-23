"""
LinuxDo 每日任务主运行器
- 先运行 CDK 登录拿到认证
- 依次执行: 薄荷 → 测试1 → 慕鸢 → Shop(多站) → NodeLoc → 黑与白(转盘/卡牌/漂流瓶)
- 任何任务出错则跳过，全部结束后重试失败任务（最多5次）
- 最后汇总推送到 Telegram
"""
import os
import sys
import time
import traceback
import logging
from datetime import datetime, timezone, timedelta

# 确保项目根目录在 path 中
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT_DIR)

from utils import load_env, send_telegram  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")

MAX_RETRIES = 5


def task_cdk_login():
    """CDK 登录（前置任务）"""
    from cdk_login import run
    ok, msg = run()
    if not ok:
        raise RuntimeError(msg)
    return msg


def task_bohe():
    """薄荷签到"""
    # 需要 cd 到薄荷目录执行（因为 session.json 路径依赖）
    saved_dir = os.getcwd()
    os.chdir(os.path.join(ROOT_DIR, "薄荷"))
    try:
        sys.path.insert(0, os.path.join(ROOT_DIR, "薄荷"))
        import importlib
        mod = importlib.import_module("签到")
        importlib.reload(mod)  # 确保重新加载
        ok, msg = mod.run()
        if not ok:
            raise RuntimeError(msg)
        return msg
    finally:
        os.chdir(saved_dir)


def task_test1():
    """测试1签到"""
    saved_dir = os.getcwd()
    os.chdir(os.path.join(ROOT_DIR, "测试1"))
    try:
        sys.path.insert(0, os.path.join(ROOT_DIR, "测试1"))
        import importlib
        mod = importlib.import_module("签到")
        importlib.reload(mod)
        ok, msg = mod.run()
        if not ok:
            raise RuntimeError(msg)
        return msg
    finally:
        os.chdir(saved_dir)


def task_muyuan():
    """慕鸢签到"""
    saved_dir = os.getcwd()
    os.chdir(os.path.join(ROOT_DIR, "慕鸢"))
    try:
        sys.path.insert(0, os.path.join(ROOT_DIR, "慕鸢"))
        import importlib
        mod = importlib.import_module("签到")
        importlib.reload(mod)
        ok, msg = mod.run()
        if not ok:
            raise RuntimeError(msg)
        return msg
    finally:
        os.chdir(saved_dir)


def task_shop():
    """Shop 签到"""
    saved_dir = os.getcwd()
    os.chdir(os.path.join(ROOT_DIR, "shop"))
    try:
        sys.path.insert(0, os.path.join(ROOT_DIR, "shop"))
        import importlib
        mod = importlib.import_module("签到")
        importlib.reload(mod)
        ok, msg = mod.run(send_tg=send_telegram)
        if not ok:
            raise RuntimeError(msg)
        return msg
    finally:
        os.chdir(saved_dir)


def task_nodeloc():
    """NodeLoc 签到"""
    saved_dir = os.getcwd()
    os.chdir(os.path.join(ROOT_DIR, "nodeloc"))
    try:
        import importlib
        mod = importlib.import_module("nodeloc.签到")
        importlib.reload(mod)
        ok, msg = mod.run()
        if not ok:
            raise RuntimeError(msg)
        return msg
    finally:
        os.chdir(saved_dir)


def task_hyb_wheel():
    """黑与白转盘"""
    saved_dir = os.getcwd()
    os.chdir(os.path.join(ROOT_DIR, "黑与白"))
    try:
        sys.path.insert(0, os.path.join(ROOT_DIR, "黑与白"))
        import importlib
        mod = importlib.import_module("转盘")
        importlib.reload(mod)
        ok, msg = mod.run()
        if not ok:
            raise RuntimeError(msg)
        return msg
    finally:
        os.chdir(saved_dir)


def task_hyb_cards():
    """黑与白卡牌"""
    saved_dir = os.getcwd()
    os.chdir(os.path.join(ROOT_DIR, "黑与白"))
    try:
        sys.path.insert(0, os.path.join(ROOT_DIR, "黑与白"))
        import importlib
        mod = importlib.import_module("卡牌")
        importlib.reload(mod)
        ok, msg = mod.run()
        if not ok:
            raise RuntimeError(msg)
        return msg
    finally:
        os.chdir(saved_dir)


def task_hyb_bottle():
    """黑与白漂流瓶"""
    saved_dir = os.getcwd()
    os.chdir(os.path.join(ROOT_DIR, "黑与白"))
    try:
        sys.path.insert(0, os.path.join(ROOT_DIR, "黑与白"))
        import importlib
        mod = importlib.import_module("漂流瓶")
        importlib.reload(mod)
        ok, msg = mod.run()
        if not ok:
            raise RuntimeError(msg)
        return msg
    finally:
        os.chdir(saved_dir)


# 任务列表（按执行顺序）
TASKS = [
    ("🌿 薄荷", task_bohe),
    ("🧪 测试1", task_test1),
    ("🕊️ 慕鸢", task_muyuan),
    ("🛍️ Shop", task_shop),
    ("🛰️ NodeLoc", task_nodeloc),
    ("🎰 黑与白转盘", task_hyb_wheel),
    ("🃏 黑与白卡牌", task_hyb_cards),
    ("🍾 黑与白漂流瓶", task_hyb_bottle),
]


def run_task(name, func):
    """运行单个任务，返回 (成功?, 结果消息)"""
    try:
        log.info(f"{'='*40}")
        log.info(f"开始执行: {name}")
        log.info(f"{'='*40}")
        msg = func()
        log.info(f"{name} 完成")
        return True, msg
    except Exception as e:
        error_msg = f"{name} 失败: {e}"
        log.error(error_msg)
        log.error(traceback.format_exc())
        return False, error_msg


def main():
    load_env()

    BJ_TZ = timezone(timedelta(hours=8))
    start_time = datetime.now(BJ_TZ)
    log.info(f"LinuxDo 每日任务开始 - {start_time.strftime('%Y-%m-%d %H:%M:%S')}")

    # Step 0: CDK 登录（前置）
    log.info("=" * 50)
    log.info("Step 0: CDK 登录")
    log.info("=" * 50)
    cdk_ok = False
    cdk_msg = ""
    try:
        cdk_msg = task_cdk_login()
        log.info(cdk_msg)
        cdk_ok = True
    except Exception as e:
        error_msg = f"CDK 登录失败: {e}"
        log.error(error_msg)
        cdk_msg = f"❌ {error_msg}"
        log.warning("CDK 登录失败，黑与白任务可能无法执行")

    # Step 1: 依次执行所有任务，失败的记录下来
    results = {}  # name -> (成功?, 消息)
    failed_tasks = []  # (name, func) 列表

    for name, func in TASKS:
        ok, msg = run_task(name, func)
        results[name] = (ok, msg)
        if not ok:
            failed_tasks.append((name, func))
        time.sleep(2)  # 任务间间隔

    # Step 2: 重试失败的任务（最多 MAX_RETRIES 次）
    # 如果有黑与白任务失败且 CDK 登录也失败，先重试 CDK 登录
    hyb_names = {"🎰 黑与白转盘", "🃏 黑与白卡牌", "🍾 黑与白漂流瓶"}
    retry_round = 0
    while failed_tasks and retry_round < MAX_RETRIES:
        retry_round += 1
        log.info(f"\n{'='*50}")
        log.info(f"重试第 {retry_round}/{MAX_RETRIES} 轮 - {len(failed_tasks)} 个失败任务")
        log.info(f"{'='*50}")

        # 如果有黑与白任务失败且 CDK 未登录成功，先重试 CDK 登录
        has_hyb_failed = any(n in hyb_names for n, _ in failed_tasks)
        if has_hyb_failed and not cdk_ok:
            log.info("重试 CDK 登录...")
            try:
                cdk_msg = task_cdk_login()
                log.info(cdk_msg)
                cdk_ok = True
            except Exception as e:
                log.error(f"CDK 重试登录失败: {e}")
                time.sleep(5)

        still_failed = []
        for name, func in failed_tasks:
            time.sleep(5)  # 重试前等待
            ok, msg = run_task(name, func)
            results[name] = (ok, msg)
            if not ok:
                still_failed.append((name, func))

        failed_tasks = still_failed
        if not failed_tasks:
            log.info("所有失败任务重试成功!")
            break

    # Step 3: 汇总消息
    end_time = datetime.now(BJ_TZ)
    duration = (end_time - start_time).total_seconds()

    success_count = sum(1 for ok, _ in results.values() if ok)
    fail_count = sum(1 for ok, _ in results.values() if not ok)

    summary_lines = [
        f"📋 LinuxDo 每日任务报告",
        f"⏰ {start_time.strftime('%Y-%m-%d %H:%M')} 北京时间 (耗时 {duration:.0f}s)",
        f"📊 成功 {success_count}/{len(TASKS)}" + (f"，失败 {fail_count}" if fail_count else ""),
        "",
        f"🔑 CDK: {cdk_msg}",
        "",
    ]

    for name, _ in TASKS:
        ok, msg = results.get(name, (False, "未执行"))
        status = "✅" if ok else "❌"
        summary_lines.append(f"{status} {name}")

    # 添加各任务详细消息
    summary_lines.append("")
    summary_lines.append("─" * 30)
    for name, _ in TASKS:
        ok, msg = results.get(name, (False, "未执行"))
        summary_lines.append(f"\n{msg}")

    if retry_round > 0:
        summary_lines.append(f"\n🔄 重试了 {retry_round} 轮")

    summary = "\n".join(summary_lines)

    # 输出到日志
    log.info(f"\n{summary}")

    # 推送到 Telegram（如果消息太长，截断）
    if len(summary) > 4000:
        summary = summary[:3950] + "\n\n... (消息过长已截断)"
    send_telegram(summary)

    log.info(f"\n任务全部完成! 耗时 {duration:.0f}s")

    # 如果有失败任务，返回非零退出码
    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()