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
            log.warning(f"获取 OAuth state 失败: HTTP {r.status_code}, {r.text[:200]}")
            return None
        try:
            data = r.json()
        except Exception:
            log.warning(f"OAuth state 响应非 JSON: {r.text[:200]}")
            return None
        if not data.get("success") or not data.get("data"):
            log.warning(f"OAuth state 响应异常: {data}")
            return None
        state = data["data"]
        log.info(f"OAuth state 获取成功: {state[:20]}...")

        authorize_url = (
            f"https://connect.linux.do/oauth2/authorize?"
            f"response_type=code&client_id={LINUXDO_CLIENT_ID}&state={state}"
        )
        delay(2, 3)
        r = self._get(authorize_url, allow_redirects=False)
        log.info(f"authorize 初始: HTTP {r.status_code}")

        max_hops = 20
        hop = 0
        current_url = authorize_url  # 正确跟踪当前 URL，不依赖 r.url
        while r.status_code in (301, 302, 303, 307, 308) and hop < max_hops:
            hop += 1
            location = r.headers.get("location", "")
            log.info(f"  hop {hop}: {r.status_code} -> {location[:120]}")
            if not location:
                break
            if location.startswith("/"):
                prev = urlparse(current_url)
                location = f"{prev.scheme}://{prev.netloc}{location}"
            current_url = location  # 更新当前 URL
            delay(0.5, 1)
            r = self._get(location, allow_redirects=False)

        # 用跟踪的 current_url 作为 final_url，不依赖 r.url（r.url 在 allow_redirects=False 时为请求 URL）
        final_url = current_url
        log.info(f"重定向结束: HTTP {r.status_code}, final_url={final_url[:120]}")

        if r.status_code == 200 and "connect.linux.do" in final_url:
            approve = re.search(r'href="(/oauth2/approve/[^"]+)"', r.text)
            if approve:
                approve_url = f"https://connect.linux.do{approve.group(1)}"
                log.info(f"发现 approve 链接，跟随: {approve_url[:80]}")
                delay(1, 2)
                r = self._get(approve_url, allow_redirects=False)
                # 继续手动跟踪重定向
                hop2 = 0
                loc = ""
                while r.status_code in (301, 302, 303, 307, 308) and hop2 < 10:
                    hop2 += 1
                    loc = r.headers.get("location", "")
                    log.info(f"  approve hop {hop2}: {r.status_code} -> {loc[:120]}")
                    if not loc:
                        break
                    if loc.startswith("/"):
                        prev = urlparse(approve_url)
                        loc = f"{prev.scheme}://{prev.netloc}{loc}"
                    final_url = loc
                    # 如果重定向目标是本站 OAuth 回调前端路由，不要请求它
                    # 请求前端路由会干扰服务器 session 中的 state 绑定，
                    # 导致后续手动调用 /api/oauth/linuxdo 时 state 不匹配
                    if BASE_URL in loc and "/oauth/linuxdo" in loc:
                        log.info(f"  检测到 OAuth 回调前端 URL，跳过请求，直接提取参数")
                        break
                    delay(0.5, 1)
                    r = self._get(loc, allow_redirects=False)
                log.info(f"approve 后: final_url={final_url[:120]}")

        code = None
        params = parse_qs(urlparse(final_url).query)
        if 'code' in params:
            code = params['code'][0]
            log.info(f"从 URL 提取到 code: {code[:20]}...")
        if not code and r.status_code == 200:
            m = re.search(r'[?&]code=([^&"\']+)', r.text or '')
            if m:
                code = m.group(1)
                log.info(f"从 body 提取到 code: {code[:20]}...")
        if not code:
            log.warning(f"未能提取 code，final_url={final_url[:200]}, body={r.text[:300]}")
            return None

        delay(2, 3)
        log.info(f"调用 OAuth 回调接口: code={code[:20]}...")
        r = self._get(f"{BASE_URL}/api/oauth/linuxdo", params={"code": code, "state": state})
        log.info(f"OAuth 回调: HTTP {r.status_code}, body={r.text[:300]}")
        try:
            return r.json()
        except Exception:
            log.warning(f"OAuth 回调响应非 JSON: {r.text[:300]}")
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
        return False, "🧪 测试1签到\n❌ OAuth 登录失败 (code 获取失败)"

    log.info(f"OAuth 回调数据: {str(data)[:300]}")
    user_data = client.extract_login_info(data)
    log.info(f"extract_login_info 结果: {str(user_data)[:200]}")
    user = client.verify_login()
    log.info(f"verify_login 结果: {str(user)[:200]}")
    if not user and not user_data:
        return False, f"🧪 测试1签到\n❌ 登录失败 - OAuth回调数据: {str(data)[:200]}"
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