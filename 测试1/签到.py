"""测试1站 (openai.api-test.us.ci) - 自动签到"""
import json
import os
import re
import sys
import time
import random
import logging
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import get_proxy, create_session, delay, load_env, CompatSession  # noqa: E402

BASE_URL = "https://openai.api-test.us.ci"
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "session.json")
IMPERSONATE_TARGETS = ["chrome133a", "chrome136", "chrome142"]
LINUXDO_CLIENT_ID = "65Lj7gYXHoSAVDDUq6Plb11thoqAV1t7"
QUOTA_PER_UNIT = 500_000
USD_EXCHANGE_RATE = 7.3

log = logging.getLogger("测试1")


def save_session(token, user_info=None):
    data = {"token": token, "user": user_info, "timestamp": time.time()}
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_session():
    if not os.path.exists(SESSION_FILE):
        return None
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if time.time() - data.get("timestamp", 0) > 86400 * 7:
            return None
        return data
    except Exception:
        return None


class NewApiClient:
    def __init__(self, proxy=None):
        self.proxy = proxy
        self.session, self.impersonate = create_session()
        self.token = None
        self.user_id = None

    def _get(self, url, **kw):
        kw.setdefault("proxy", self.proxy)
        kw.setdefault("timeout", 30)
        return self.session.get(url, **kw)

    def _post(self, url, **kw):
        kw.setdefault("proxy", self.proxy)
        kw.setdefault("timeout", 30)
        return self.session.post(url, **kw)

    def _api_get(self, url, **kw):
        kw.setdefault("proxy", self.proxy)
        kw.setdefault("timeout", 30)
        if self.user_id:
            headers = kw.get("headers", {})
            headers["New-Api-User"] = str(self.user_id)
            kw["headers"] = headers
        return self.session.get(url, **kw)

    def _api_post(self, url, **kw):
        kw.setdefault("proxy", self.proxy)
        kw.setdefault("timeout", 30)
        if self.user_id:
            headers = kw.get("headers", {})
            headers["New-Api-User"] = str(self.user_id)
            kw["headers"] = headers
        return self.session.post(url, **kw)

    def login_linuxdo(self, username, password):
        # 直接请求 CSRF API，跳过首页避免 Cloudflare 拦截
        r = self._get("https://linux.do/session/csrf.json")
        if r.status_code == 403:
            for alt in IMPERSONATE_TARGETS:
                if alt == self.impersonate:
                    continue
                self.impersonate = alt
                self.session = CompatSession(impersonate=alt)
                delay(2, 4)
                r = self._get("https://linux.do/session/csrf.json")
                if r.status_code == 200:
                    break
        if r.status_code != 200:
            return False
        csrf = r.json().get("csrf")
        delay(1, 2)
        r = self._post(
            "https://linux.do/session",
            data={"login": username, "password": password, "second_factor_method": "1"},
            headers={"X-CSRF-Token": csrf, "X-Requested-With": "XMLHttpRequest"},
        )
        if r.status_code == 200:
            try:
                data = r.json()
                if data.get("user", {}).get("username"):
                    return True
            except Exception:
                pass
        return False

    def oauth_login(self):
        delay(2, 3)
        r = self._api_get(f"{BASE_URL}/api/oauth/state")
        if r.status_code != 200:
            return None
        data = r.json()
        if not data.get("success") or not data.get("data"):
            return None
        state = data["data"]

        authorize_url = (
            f"https://connect.linux.do/oauth2/authorize?"
            f"response_type=code&client_id={LINUXDO_CLIENT_ID}&state={state}"
        )
        delay(2, 3)
        r = self._get(authorize_url, allow_redirects=False)

        max_hops = 20
        hop = 0
        while r.status_code in (301, 302, 303, 307, 308) and hop < max_hops:
            hop += 1
            location = r.headers.get("location", "")
            if location.startswith("/"):
                prev = urlparse(str(getattr(r, 'url', authorize_url)))
                location = f"{prev.scheme}://{prev.netloc}{location}"
            delay(0.5, 1)
            r = self._get(location, allow_redirects=False)

        final_url = str(getattr(r, 'url', '') or '')

        if r.status_code == 200 and "connect.linux.do" in final_url:
            approve = re.search(r'href="(/oauth2/approve/[^"]+)"', r.text)
            if approve:
                delay(1, 2)
                r = self._get(f"https://connect.linux.do{approve.group(1)}", allow_redirects=True)
                final_url = str(getattr(r, 'url', '') or '')

        code = None
        params = parse_qs(urlparse(final_url).query)
        if 'code' in params:
            code = params['code'][0]
        if not code and r.status_code == 200:
            m = re.search(r'[?&]code=([^&"]+)', r.text or '')
            if m:
                code = m.group(1)
        if not code:
            return None

        delay(2, 3)
        r = self._get(f"{BASE_URL}/api/oauth/linuxdo", params={"code": code, "state": state})
        try:
            return r.json()
        except Exception:
            return None

    def extract_login_info(self, data):
        if not isinstance(data, dict):
            return None
        if data.get("success") and data.get("data"):
            user_data = data["data"]
            if isinstance(user_data, dict) and user_data.get("id"):
                self.user_id = user_data["id"]
            return user_data
        session_cookie = self.session.cookies.get("session")
        if session_cookie:
            self.token = session_cookie
        return None

    def verify_login(self):
        r = self._api_get(f"{BASE_URL}/api/user/self")
        if r.status_code == 200:
            try:
                data = r.json()
                if data.get("success") and data.get("data"):
                    return data["data"]
            except Exception:
                pass
        return None

    def get_quota(self):
        r = self._api_get(f"{BASE_URL}/api/user/self")
        if r.status_code == 200:
            try:
                data = r.json()
                if data.get("success") and data.get("data"):
                    u = data["data"]
                    return {"quota": u.get("quota", 0), "used_quota": u.get("used_quota", 0)}
            except Exception:
                pass
        return None

    def checkin(self):
        delay(2, 4)
        r = self._api_post(f"{BASE_URL}/api/user/checkin")
        try:
            return r.json()
        except Exception:
            return {"success": False, "message": f"HTTP {r.status_code}"}


def fmt_quota(q):
    if q is None:
        return "未知"
    usd = q / QUOTA_PER_UNIT
    cny = usd * USD_EXCHANGE_RATE
    return f"¥{cny:.2f}"


def build_msg(uname, result, quota_info=None):
    lines = ["🧪 测试1签到", f"用户: {uname}"]
    success = result.get("success", False)
    msg_text = result.get("message", str(result))
    checkin_data = result.get("data", {}) if isinstance(result.get("data"), dict) else {}
    if success:
        lines.append(f"✅ {msg_text}")
        awarded = checkin_data.get("quota_awarded")
        if awarded:
            lines.append(f"🎁 签到奖励: {fmt_quota(awarded)}")
    else:
        lines.append(f"⚠️ {msg_text}")
    if quota_info:
        total = quota_info.get("quota", 0)
        used = quota_info.get("used_quota", 0)
        lines.append(f"💰 总额度: {fmt_quota(total)}")
        lines.append(f"📊 已使用: {fmt_quota(used)}")
        lines.append(f"🔋 剩余: {fmt_quota(total - used)}")
    return "\n".join(lines)


def run():
    """执行测试1签到，返回 (成功?, 消息)"""
    username = os.environ.get("CDK_USERNAME")
    password = os.environ.get("CDK_PASSWORD")
    if not username or not password:
        return False, "🧪 测试1签到\n❌ CDK_USERNAME 或 CDK_PASSWORD 未设置"

    proxy = get_proxy()
    client = NewApiClient(proxy=proxy)

    saved = load_session()
    if saved and saved.get("token"):
        client.session.cookies.set("session", saved["token"], domain="openai.api-test.us.ci")
        client.token = saved["token"]
        saved_user = saved.get("user")
        if isinstance(saved_user, dict) and saved_user.get("id"):
            client.user_id = saved_user["id"]
        user = client.verify_login()
        if user:
            result = client.checkin()
            uname = user.get("display_name") or user.get("username") or "未知"
            quota_info = client.get_quota()
            return True, build_msg(uname, result, quota_info)

    if not client.login_linuxdo(username, password):
        return False, "🧪 测试1签到\n❌ LinuxDo 登录失败"

    data = client.oauth_login()
    if not data:
        return False, "🧪 测试1签到\n❌ OAuth 登录失败"

    user_data = client.extract_login_info(data)
    user = client.verify_login()
    if not user and not user_data:
        return False, "🧪 测试1签到\n❌ 登录失败 - 无法验证身份"
    if not user:
        user = user_data

    session_cookie = client.session.cookies.get("session")
    if session_cookie:
        save_session(session_cookie, user)

    result = client.checkin()
    uname = (user or {}).get("display_name") or (user or {}).get("username") or "未知"
    quota_info = client.get_quota()
    return True, build_msg(uname, result, quota_info)


def main():
    load_env()
    from utils import send_telegram
    ok, msg = run()
    log.info(msg)
    send_telegram(msg)


if __name__ == "__main__":
    main()