"""黑与白 CDK 站共享模块"""
import json
import os
import sys
import time
import logging

from curl_cffi import requests as cffi_requests

# 添加父目录到 path 以导入 utils
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import get_proxy, create_session, delay  # noqa: E402

BASE_URL = "https://cdk.hybgzs.com"
SESSION_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "session.json")

log = logging.getLogger("黑与白")

CDK_COOKIE_NAMES = {
    "__Host-authjs.csrf-token",
    "__Secure-authjs.callback-url",
    "server_name_session",
    "__Secure-authjs.pkce.code_verifier",
    "__Secure-authjs.session-token",
}


def load_session_cookies(session):
    if not os.path.exists(SESSION_FILE):
        log.error(f"未找到 session 文件: {SESSION_FILE}")
        raise RuntimeError("请先运行 cdk_login 登录")
    with open(SESSION_FILE, "r", encoding="utf-8") as f:
        saved = json.load(f)
    if time.time() - saved.get("timestamp", 0) > 86400:
        raise RuntimeError("Session 已过期 (>24h)，请重新登录")
    for name, value in saved.get("cookies", {}).items():
        if name in CDK_COOKIE_NAMES:
            session.cookies.set(name, value)
    return saved


def _dedup_cookies(session):
    seen = {}
    for name, value in session.cookies.items():
        seen[name] = value
    session.cookies.clear()
    for name, value in seen.items():
        session.cookies.set(name, value)


def verify_session(session, proxy):
    r = session.get(f"{BASE_URL}/api/auth/session", proxy=proxy, timeout=15)
    if r.status_code == 200:
        data = r.json()
        if data and data.get("user"):
            user = data["user"]
            log.info(f"已登录: {user.get('name')} (provider: {user.get('provider')})")
            _dedup_cookies(session)
            return user
    raise RuntimeError("CDK Session 无效")


def init():
    session, imp = create_session()
    proxy = get_proxy()
    log.info(f"浏览器指纹: {imp}")
    load_session_cookies(session)
    user = verify_session(session, proxy)
    return session, proxy, user


def fmt_quota(amount):
    if amount is None:
        return "N/A"
    return f"${amount / 500000:.1f}"