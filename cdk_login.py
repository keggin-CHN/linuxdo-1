"""LinuxDo 协议登录 + CDK OAuth 认证"""
import json
import os
import re
import sys
import time
import logging
from urllib.parse import urlparse

from curl_cffi import requests as cffi_requests
from utils import load_env, get_proxy, create_session, delay

BASE_URL = "https://cdk.hybgzs.com"
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "session.json")
IMPERSONATE_TARGETS = ["chrome133a", "chrome136", "chrome142"]

log = logging.getLogger("cdk_login")


class CDKClient:
    def __init__(self, proxy=None):
        self.proxy = proxy
        self.session, self.impersonate = create_session()
        log.info(f"浏览器指纹: {self.impersonate}")

    def _get(self, url, **kwargs):
        kwargs.setdefault("proxy", self.proxy)
        kwargs.setdefault("timeout", 30)
        return self.session.get(url, **kwargs)

    def _post(self, url, **kwargs):
        kwargs.setdefault("proxy", self.proxy)
        kwargs.setdefault("timeout", 30)
        return self.session.post(url, **kwargs)

    def get_cdk_csrf(self):
        r = self._get(f"{BASE_URL}/api/auth/csrf")
        data = r.json()
        token = data.get("csrfToken")
        log.info(f"CDK CSRF token: {token[:20]}...")
        return token

    def initiate_oauth(self, provider, csrf_token):
        log.info(f"发起 OAuth 登录 (provider: {provider})...")
        r = self._post(
            f"{BASE_URL}/api/auth/signin/{provider}",
            data={"csrfToken": csrf_token},
            allow_redirects=False,
        )
        if r.status_code in (302, 303, 307):
            location = r.headers.get("location", "")
            log.info(f"OAuth 重定向: {location[:100]}...")
            return location
        if r.status_code == 200:
            try:
                data = r.json()
                url = data.get("url")
                if url:
                    return url
            except Exception:
                pass
        log.error(f"OAuth 发起失败: status={r.status_code}")
        return None

    def login_linuxdo(self, username, password):
        log.info("开始 LinuxDo 登录...")
        # 先访问首页建立 session，如果 403 则切换指纹
        r = self._get("https://linux.do/", allow_redirects=True)
        log.info(f"LinuxDo 首页: {r.status_code}")
        if r.status_code == 403:
            for alt in IMPERSONATE_TARGETS:
                if alt == self.impersonate:
                    continue
                log.info(f"切换指纹: {alt}")
                self.impersonate = alt
                self.session = cffi_requests.Session(impersonate=alt)
                delay(2, 4)
                r = self._get("https://linux.do/", allow_redirects=True)
                log.info(f"LinuxDo 首页 ({alt}): {r.status_code}")
                if r.status_code == 200:
                    break
            if r.status_code != 200:
                log.error(f"所有指纹均被 403")
                return False
        delay(1, 2)
        r = self._get("https://linux.do/session/csrf.json")
        if r.status_code != 200:
            log.error(f"获取 LinuxDo CSRF 失败: {r.status_code}")
            return False
        try:
            csrf = r.json().get("csrf")
        except Exception:
            log.error(f"CSRF 响应非 JSON: {r.text[:200]}")
            return False
        log.info(f"LinuxDo CSRF: {csrf[:20]}...")
        r = self._post(
            "https://linux.do/session",
            data={
                "login": username,
                "password": password,
                "second_factor_method": "1",
            },
            headers={
                "X-CSRF-Token": csrf,
                "X-Requested-With": "XMLHttpRequest",
                "Origin": "https://linux.do",
                "Referer": "https://linux.do/login",
            },
        )
        log.info(f"LinuxDo 登录响应: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            if data.get("error"):
                log.error(f"登录错误: {data['error']}")
                return False
            user = data.get("user", {})
            if user.get("username"):
                log.info(f"LinuxDo 登录成功! 用户: {user['username']}")
                return True
        log.error(f"LinuxDo 登录失败: {r.text[:300]}")
        return False

    def _follow_redirects(self, r, base_url, max_redirects=15):
        redirect_count = 0
        current_base = base_url
        current_url = base_url  # 跟踪当前实际 URL
        while r.status_code in (301, 302, 303, 307, 308) and redirect_count < max_redirects:
            location = r.headers.get("location", "")
            redirect_count += 1
            if location.startswith("/"):
                parsed = urlparse(current_base)
                location = f"{parsed.scheme}://{parsed.netloc}{location}"
            current_base = location
            current_url = location
            log.info(f"  重定向 #{redirect_count}: {location[:120]}...")
            parsed_loc = urlparse(location)
            if parsed_loc.netloc == "cdk.hybgzs.com":
                r = self._get(location, allow_redirects=True)
                r._current_url = str(getattr(r, 'url', '') or location)
                return r
            r = self._get(location, allow_redirects=False)
        r._current_url = current_url
        return r

    def follow_oauth_flow(self, authorize_url):
        log.info("跟随 OAuth 授权流程...")
        # 先访问 connect.linux.do 首页预热 Cloudflare session
        log.info("预热 connect.linux.do...")
        r = self._get("https://connect.linux.do/", allow_redirects=True)
        log.info(f"connect.linux.do 首页: {r.status_code}")
        delay(1, 2)
        r = self._get(authorize_url, allow_redirects=False)
        r = self._follow_redirects(r, authorize_url)
        current_url = getattr(r, '_current_url', '') or str(getattr(r, 'url', '') or '')
        log.info(f"OAuth 流程停在: {current_url[:120]} (status={r.status_code})")

        # 检查是否在 connect.linux.do 的授权页面
        if r.status_code == 200 and "connect.linux.do" in current_url:
            approve_match = re.search(r'href="(/oauth2/approve/[^"]+)"', r.text)
            if approve_match:
                approve_path = approve_match.group(1)
                approve_url = f"https://connect.linux.do{approve_path}"
                log.info(f"点击授权: {approve_url}")
                r = self._get(approve_url, allow_redirects=False)
                r = self._follow_redirects(r, approve_url)
                current_url = getattr(r, '_current_url', '') or str(getattr(r, 'url', '') or '')
                log.info(f"授权后停在: {current_url[:120]} (status={r.status_code})")

        # 如果还在 connect.linux.do 的 sso_callback 页面，检查是否有自动跳转
        if r.status_code == 200 and "connect.linux.do" in current_url:
            # 可能页面里有 meta refresh 或 JS 跳转
            meta_match = re.search(r'url=([^"\'>\s]+)', r.text, re.IGNORECASE)
            if meta_match:
                redirect_url = meta_match.group(1)
                if redirect_url.startswith("/"):
                    redirect_url = f"https://connect.linux.do{redirect_url}"
                log.info(f"Meta 跳转: {redirect_url[:120]}")
                r = self._get(redirect_url, allow_redirects=False)
                r = self._follow_redirects(r, redirect_url)

        return r

    def check_session(self):
        r = self._get(f"{BASE_URL}/api/auth/session")
        if r.status_code == 200:
            data = r.json()
            if data and isinstance(data, dict) and data.get("user"):
                return data
        return None

    def extract_cookies(self):
        cookies = {}
        jar = self.session.cookies
        if hasattr(jar, 'items'):
            for name, value in jar.items():
                cookies[name] = value
        else:
            for cookie in jar:
                if hasattr(cookie, 'name'):
                    cookies[cookie.name] = cookie.value
        return cookies

    def save_session(self, session_data, cookies):
        info = {
            "session": session_data,
            "cookies": cookies,
            "timestamp": time.time(),
        }
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2, ensure_ascii=False)
        log.info(f"Session 已保存到 {SESSION_FILE}")

    def load_session(self):
        if not os.path.exists(SESSION_FILE):
            return None
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if time.time() - data.get("timestamp", 0) > 86400:
                log.info("Session 已过期 (>24h)")
                return None
            return data
        except Exception:
            return None


def run():
    """执行 CDK 登录，返回 (成功?, 消息)"""
    username = os.environ.get("CDK_USERNAME")
    password = os.environ.get("CDK_PASSWORD")
    if not username or not password:
        return False, "CDK_USERNAME 或 CDK_PASSWORD 未设置"

    proxy = get_proxy()
    client = CDKClient(proxy=proxy)

    saved = client.load_session()
    if saved and saved.get("session"):
        log.info("发现已保存的 session，验证中...")
        for name, value in saved.get("cookies", {}).items():
            client.session.cookies.set(name, value)
        session_data = client.check_session()
        if session_data and session_data.get("user"):
            log.info("Session 有效!")
            return True, "CDK Session 有效（缓存）"

    csrf_token = client.get_cdk_csrf()

    if not client.login_linuxdo(username, password):
        return False, "LinuxDo 登录失败"

    authorize_url = client.initiate_oauth("linuxdo", csrf_token)
    if not authorize_url:
        return False, "无法获取 OAuth 授权 URL"

    client.follow_oauth_flow(authorize_url)

    session_data = client.check_session()
    cookies = client.extract_cookies()

    if session_data and session_data.get("user"):
        client.save_session(session_data, cookies)
        return True, f"CDK 登录成功: {session_data['user'].get('name', '未知')}"
    else:
        return False, "CDK 登录失败 - 无法获取 session"


def main():
    load_env()
    ok, msg = run()
    log.info(msg)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()