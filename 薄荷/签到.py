"""薄荷公益站 (qd.x666.me) - 自动签到（转盘）"""
import json
import os
import re
import sys
import time
import random
import logging
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import get_proxy, create_session, delay, load_env  # noqa: E402
from curl_cffi import requests as cffi_requests  # noqa: E402

BASE_URL = "https://qd.x666.me"
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "session.json")
IMPERSONATE_TARGETS = ["chrome133a", "chrome136", "chrome142"]
QUOTA_PER_TIME = 500

log = logging.getLogger("薄荷")


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


class BoHeClient:
    def __init__(self, proxy=None):
        self.proxy = proxy
        self.session, self.impersonate = create_session()
        log.info(f"浏览器指纹: {self.impersonate}")
        self.jwt_token = None

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
        if self.jwt_token:
            headers = kw.get("headers", {})
            headers["Authorization"] = f"Bearer {self.jwt_token}"
            kw["headers"] = headers
        return self.session.get(url, **kw)

    def _api_post(self, url, **kw):
        kw.setdefault("proxy", self.proxy)
        kw.setdefault("timeout", 30)
        if self.jwt_token:
            headers = kw.get("headers", {})
            headers["Authorization"] = f"Bearer {self.jwt_token}"
            kw["headers"] = headers
        return self.session.post(url, **kw)

    def login_linuxdo(self, username, password):
        log.info("登录 linux.do...")
        # 直接请求 CSRF API，跳过首页避免 Cloudflare 拦截
        r = self._get("https://linux.do/session/csrf.json")
        if r.status_code == 403:
            for alt in IMPERSONATE_TARGETS:
                if alt == self.impersonate:
                    continue
                self.impersonate = alt
                self.session = cffi_requests.Session(impersonate=alt)
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
                    log.info(f"LinuxDo 登录成功: {data['user']['username']}")
                    return True
            except Exception:
                pass
        return False

    def oauth_login(self):
        log.info("发起薄荷站 OAuth 登录...")
        delay(2, 3)
        r = self._get(f"{BASE_URL}/api/auth/login")
        if r.status_code != 200:
            return None
        try:
            auth_url = r.json().get("auth_url")
        except Exception:
            return None
        if not auth_url:
            return None

        delay(1, 2)
        r = self._get(auth_url, allow_redirects=False)

        max_hops = 20
        hop = 0
        while r.status_code in (301, 302, 303, 307, 308) and hop < max_hops:
            hop += 1
            location = r.headers.get("location", "")
            if "token=" in location:
                parsed = urlparse(location)
                params = parse_qs(parsed.query)
                if "token" in params:
                    self.jwt_token = params["token"][0]
                    return {"success": True, "token": self.jwt_token}
            if location.startswith("/"):
                prev = urlparse(str(getattr(r, 'url', auth_url)))
                location = f"{prev.scheme}://{prev.netloc}{location}"
            delay(0.5, 1)
            r = self._get(location, allow_redirects=False)

        final_url = str(getattr(r, 'url', '') or '')
        if "token=" in final_url:
            params = parse_qs(urlparse(final_url).query)
            if "token" in params:
                self.jwt_token = params["token"][0]
                return {"success": True, "token": self.jwt_token}

        if r.status_code == 200 and "connect.linux.do" in final_url:
            approve = re.search(r'href="(/oauth2/approve/[^"]+)"', r.text)
            if approve:
                delay(1, 2)
                r = self._get(f"https://connect.linux.do{approve.group(1)}", allow_redirects=False)
                while r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers.get("location", "")
                    if "token=" in loc:
                        params = parse_qs(urlparse(loc).query)
                        if "token" in params:
                            self.jwt_token = params["token"][0]
                            return {"success": True, "token": self.jwt_token}
                    if loc.startswith("/"):
                        prev = urlparse(str(getattr(r, 'url', '')))
                        loc = f"{prev.scheme}://{prev.netloc}{loc}"
                    delay(0.5, 1)
                    r = self._get(loc, allow_redirects=False)
                final_url = str(getattr(r, 'url', '') or '')
                if "token=" in final_url:
                    params = parse_qs(urlparse(final_url).query)
                    if "token" in params:
                        self.jwt_token = params["token"][0]
                        return {"success": True, "token": self.jwt_token}

        return None

    def verify_login(self):
        r = self._api_get(f"{BASE_URL}/api/user/info")
        if r.status_code == 200:
            try:
                data = r.json()
                if data.get("success"):
                    return data
            except Exception:
                pass
        return None

    def get_checkin_status(self):
        r = self._api_get(f"{BASE_URL}/api/checkin/status")
        if r.status_code == 200:
            try:
                data = r.json()
                if data.get("success"):
                    return data
            except Exception:
                pass
        return None

    def spin(self):
        delay(2, 4)
        r = self._api_post(f"{BASE_URL}/api/checkin/spin")
        try:
            return r.json()
        except Exception:
            return {"success": False, "message": f"HTTP {r.status_code}"}


def fmt_times(quota):
    if quota is None:
        return "未知"
    return f"{quota // QUOTA_PER_TIME:,} 次"


def build_msg(uname, result, status=None):
    lines = ["🌿 薄荷签到", f"用户: {uname}"]
    if result.get("success"):
        lines.append("✅ 签到成功")
        label = result.get("label", "")
        quota = result.get("quota", 0)
        new_balance = result.get("new_balance", 0)
        if label:
            lines.append(f"🎰 奖项: {label}")
        if quota:
            lines.append(f"🎁 获得: {fmt_times(quota)}")
        if new_balance:
            lines.append(f"💰 余额: {fmt_times(new_balance)}")
    else:
        lines.append(f"⚠️ {result.get('message', str(result))}")
    if status:
        total = status.get("total_quota", 0)
        current = status.get("current_quota", 0)
        if total:
            lines.append(f"📊 累计获得: {fmt_times(total)}")
        if current:
            lines.append(f"🔋 当前余额: {fmt_times(current)}")
    return "\n".join(lines)


def run():
    """执行薄荷签到，返回 (成功?, 消息)"""
    username = os.environ.get("CDK_USERNAME")
    password = os.environ.get("CDK_PASSWORD")
    if not username or not password:
        return False, "🌿 薄荷签到\n❌ CDK_USERNAME 或 CDK_PASSWORD 未设置"

    proxy = get_proxy()
    client = BoHeClient(proxy=proxy)

    saved = load_session()
    if saved and saved.get("token"):
        client.jwt_token = saved["token"]
        user = client.verify_login()
        if user:
            status = client.get_checkin_status()
            if status and status.get("can_spin") is False:
                msg = build_msg(user.get("username", "未知"),
                                {"success": False, "message": "今日已签到"}, status)
                return True, msg
            result = client.spin()
            status = client.get_checkin_status()
            msg = build_msg(user.get("username", "未知"), result, status)
            return True, msg

    if not client.login_linuxdo(username, password):
        return False, "🌿 薄荷签到\n❌ LinuxDo 登录失败"

    data = client.oauth_login()
    if not data or not data.get("success"):
        return False, "🌿 薄荷签到\n❌ OAuth 登录失败"

    user = client.verify_login()
    if not user:
        return False, "🌿 薄荷签到\n❌ JWT 验证失败"

    save_session(client.jwt_token, user)

    status = client.get_checkin_status()
    if status and status.get("can_spin") is False:
        msg = build_msg(user.get("username", "未知"),
                        {"success": False, "message": "今日已签到"}, status)
        return True, msg

    result = client.spin()
    status = client.get_checkin_status()
    msg = build_msg(user.get("username", "未知"), result, status)
    return True, msg


def main():
    load_env()
    from utils import send_telegram
    ok, msg = run()
    log.info(msg)
    send_telegram(msg)


if __name__ == "__main__":
    main()