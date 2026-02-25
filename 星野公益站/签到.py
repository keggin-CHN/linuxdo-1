"""星野公益站 (api.hoshino.edu.rs) 自动签到（LinuxDo OAuth + new-api）"""
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import CompatSession, create_session, delay, load_env, send_telegram  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("星野公益站")

BASE_URL = "https://api.hoshino.edu.rs"
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "session.json")
IMPERSONATE_TARGETS = ["chrome133a", "chrome136", "chrome142"]

# Ubuntu 直连，无需本地代理；本地调试可通过环境变量 HOSHINO_PROXY / TG_PROXY 覆盖
HARDCODED_PROXY = ""   # 默认直连
LINUXDO_PROXY = None   # linux.do / connect.linux.do 直连
HARDCODED_TG_BOT_TOKEN = "7483346980:AAHT4LBRiDU0H617sRQZmNUL8A6GumybMHE"
HARDCODED_TG_CHAT_ID = "7420206850"
DEFAULT_LINUXDO_CLIENT_ID = "XPXmWksr3NcH2aiz0MgqK5jtEmfdfZ0Q"


def esc(text, n=360):
    return (text or "")[:n].encode("unicode_escape").decode("ascii")


def save_session(session_cookie, user_info=None, access_token="", user_id=None):
    data = {
        "session_cookie": session_cookie or "",
        "access_token": access_token or "",
        "user_id": user_id,
        "user": user_info or {},
        "timestamp": time.time(),
    }
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_session():
    if not os.path.exists(SESSION_FILE):
        return None
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if time.time() - float(data.get("timestamp", 0)) > 86400 * 7:
            return None
        return data
    except Exception:
        return None


def deep_find_first_str(obj, key_candidates):
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in key_candidates and isinstance(v, str) and v.strip():
                return v.strip()
        for v in obj.values():
            got = deep_find_first_str(v, key_candidates)
            if got:
                return got
    elif isinstance(obj, list):
        for item in obj:
            got = deep_find_first_str(item, key_candidates)
            if got:
                return got
    return ""


class HoshinoClient:
    def __init__(self, proxy=None):
        self.proxy = proxy or None
        self.session, self.impersonate = create_session()
        self.access_token = ""
        self.user_id = None

    def _url(self, path):
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return BASE_URL + path

    def _auth_headers(self, headers=None):
        h = dict(headers or {})
        if self.access_token:
            h["Authorization"] = f"Bearer {self.access_token}"
        if self.user_id is not None:
            h["New-Api-User"] = str(self.user_id)
        return h

    def _get(self, path, **kwargs):
        kwargs.setdefault("proxy", self.proxy)
        kwargs.setdefault("timeout", 30)
        return self.session.get(self._url(path), **kwargs)

    def _post(self, path, **kwargs):
        kwargs.setdefault("proxy", self.proxy)
        kwargs.setdefault("timeout", 30)
        return self.session.post(self._url(path), **kwargs)

    def _api_get(self, path, **kwargs):
        kwargs["headers"] = self._auth_headers(kwargs.get("headers"))
        return self._get(path, **kwargs)

    def _api_post(self, path, **kwargs):
        kwargs["headers"] = self._auth_headers(kwargs.get("headers"))
        return self._post(path, **kwargs)

    def _json(self, r):
        try:
            return r.json()
        except Exception:
            return None

    def _log_resp(self, tag, r, n=320):
        log.info("=" * 78)
        log.info("[%s] %s", tag, getattr(r, "url", ""))
        log.info("status=%s ct=%s", r.status_code, r.headers.get("content-type"))
        log.info("location=%s", r.headers.get("location"))
        log.info("cookies=%s", dict(self.session.cookies))
        log.info("body[:%d]=%s", n, esc(r.text, n))
        log.info("=" * 78)

    def _update_auth_from_json(self, data):
        if not isinstance(data, dict):
            return
        token = deep_find_first_str(
            data,
            {"token", "access_token", "accesstoken", "jwt", "bearer"},
        )
        if token:
            self.access_token = token
        data_obj = data.get("data")
        if isinstance(data_obj, dict) and isinstance(data_obj.get("id"), int):
            self.user_id = data_obj["id"]
            return
        user_obj = data.get("user")
        if isinstance(user_obj, dict) and isinstance(user_obj.get("id"), int):
            self.user_id = user_obj["id"]

    def login_linuxdo(self, username, password):
        if not username or not password:
            return False

        r = self.session.get(
            "https://linux.do/session/csrf.json",
            proxy=LINUXDO_PROXY,
            timeout=30,
            allow_redirects=False,
        )
        self._log_resp("linux.do csrf", r, n=220)

        if r.status_code == 403:
            for alt in IMPERSONATE_TARGETS:
                if alt == self.impersonate:
                    continue
                self.impersonate = alt
                self.session = CompatSession(impersonate=alt)
                delay(1, 2)
                r = self.session.get(
                    "https://linux.do/session/csrf.json",
                    proxy=LINUXDO_PROXY,
                    timeout=30,
                    allow_redirects=False,
                )
                self._log_resp(f"linux.do csrf ({alt})", r, n=220)
                if r.status_code == 200:
                    break

        if r.status_code != 200:
            return False

        j = self._json(r)
        csrf = (j.get("csrf") if isinstance(j, dict) else "") or ""
        if not csrf:
            return False

        delay(1, 2)
        lr = self.session.post(
            "https://linux.do/session",
            proxy=LINUXDO_PROXY,
            timeout=30,
            allow_redirects=False,
            data={"login": username, "password": password, "second_factor_method": "1"},
            headers={
                "X-CSRF-Token": csrf,
                "X-Requested-With": "XMLHttpRequest",
                "Origin": "https://linux.do",
                "Referer": "https://linux.do/login",
            },
        )
        self._log_resp("linux.do login", lr)
        data = self._json(lr)
        return bool(isinstance(data, dict) and data.get("user", {}).get("username"))

    def get_oauth_state(self):
        r = self._api_get("/api/oauth/state", allow_redirects=False)
        self._log_resp("GET /api/oauth/state", r, n=260)
        j = self._json(r)
        if isinstance(j, dict) and j.get("success"):
            return str(j.get("data") or "").strip()
        return ""

    def discover_client_id(self):
        r = self._api_get("/api/status", allow_redirects=False)
        self._log_resp("GET /api/status", r, n=260)
        j = self._json(r)
        if isinstance(j, dict):
            data = j.get("data")
            if isinstance(data, dict):
                cid = str(data.get("linuxdo_client_id") or "").strip()
                if cid:
                    return cid
        m = re.search(r'"linuxdo_client_id"\s*:\s*"([A-Za-z0-9_-]{6,})"', r.text or "", re.I)
        if m:
            return m.group(1).strip()
        return DEFAULT_LINUXDO_CLIENT_ID

    def _extract_code_from_url(self, u):
        try:
            q = parse_qs(urlparse(u).query)
            if "code" in q and q["code"]:
                return q["code"][0]
        except Exception:
            pass
        return ""

    def _prewarm_connect(self):
        """预热 connect.linux.do，写入 auth.session-token"""
        try:
            r = self.session.get(
                "https://connect.linux.do/oauth2/authorize?response_type=code&client_id=linux_do",
                proxy=LINUXDO_PROXY,
                timeout=30,
                allow_redirects=True,
            )
            token = self.session.cookies.get("auth.session-token")
            return bool(token) or (r.status_code in (200, 302, 303, 401))
        except Exception:
            return False

    def login_site_by_oauth(self):
        state = self.get_oauth_state()
        if not state:
            return False

        client_id = self.discover_client_id()
        log.info("OAuth client_id=%s", client_id)

        self._prewarm_connect()
        delay(0.5, 1)

        authorize_url = "https://connect.linux.do/oauth2/authorize?" + urlencode(
            {"response_type": "code", "client_id": client_id, "state": state}
        )
        log.info("authorize_url=%s", authorize_url)

        history = []
        current = authorize_url
        last = None
        for i in range(30):
            use_proxy = LINUXDO_PROXY if "connect.linux.do" in current or "linux.do" in current else self.proxy
            r = self.session.get(
                current,
                proxy=use_proxy,
                timeout=30,
                allow_redirects=False,
            )
            last = r
            loc = r.headers.get("location", "")
            final_url = str(getattr(r, "url", "") or current)
            history.append((final_url, r.status_code, loc))
            log.info("oauth hop#%d status=%s %s -> %s", i + 1, r.status_code, final_url, loc)

            if r.status_code == 403 and "connect.linux.do" in final_url:
                log.warning("connect.linux.do 403，尝试切换指纹")
                for alt in IMPERSONATE_TARGETS:
                    if alt == self.impersonate:
                        continue
                    old_cookies = []
                    try:
                        for c in self.session.cookies.jar:
                            old_cookies.append((c.name, c.value, c.domain, c.path))
                    except Exception:
                        if hasattr(self.session.cookies, "items"):
                            for k, v in self.session.cookies.items():
                                old_cookies.append((k, v, None, "/"))
                    self.impersonate = alt
                    self.session = CompatSession(impersonate=alt)
                    for name, value, domain, path in old_cookies:
                        kw = {}
                        if domain:
                            kw["domain"] = domain
                        if path:
                            kw["path"] = path
                        self.session.cookies.set(name, value, **kw)
                    delay(1, 2)
                    r = self.session.get(
                        current,
                        proxy=LINUXDO_PROXY,
                        timeout=30,
                        allow_redirects=False,
                    )
                    log.info("oauth hop#%d (retry %s) status=%s", i + 1, alt, r.status_code)
                    if r.status_code != 403:
                        last = r
                        loc = r.headers.get("location", "")
                        final_url = str(getattr(r, "url", "") or current)
                        history.append((final_url, r.status_code, loc))
                        break
                if r.status_code == 403:
                    log.error("connect.linux.do 持续 403，OAuth 失败")
                    return False

            if r.status_code in (301, 302, 303, 307, 308) and loc:
                current = urljoin(final_url, loc)
                continue

            if r.status_code == 200 and "connect.linux.do" in final_url:
                approve = re.search(r'href="(/oauth2/approve/[^"]+)"', r.text or "")
                if approve:
                    current = urljoin(final_url, approve.group(1))
                    continue
            break

        final_url = history[-1][0] if history else ""
        code = self._extract_code_from_url(final_url)

        if not code:
            for u, _, loc in history:
                code = self._extract_code_from_url(u)
                if code:
                    break
                if loc:
                    code = self._extract_code_from_url(urljoin(u, loc))
                    if code:
                        break

        if not code and last is not None and last.status_code == 200:
            m = re.search(r"[?&]code=([^&\"']+)", last.text or "")
            if m:
                code = m.group(1).strip()

        if not code:
            log.error("OAuth 未提取到 code")
            return False

        cb = "/api/oauth/linuxdo?" + urlencode({"code": code, "state": state})
        cr = self._api_get(cb, allow_redirects=False)
        self._log_resp("GET /api/oauth/linuxdo callback", cr, n=360)
        j = self._json(cr)
        self._update_auth_from_json(j)

        user = self.get_self()
        return bool(user)

    def get_self(self):
        r = self._api_get("/api/user/self", allow_redirects=False)
        self._log_resp("GET /api/user/self", r, n=320)
        j = self._json(r)
        self._update_auth_from_json(j)
        if isinstance(j, dict) and j.get("success") is True and isinstance(j.get("data"), dict):
            return j["data"]
        return None

    def get_checkin_status(self):
        month = datetime.now().strftime("%Y-%m")
        r = self._api_get("/api/user/checkin", params={"month": month}, allow_redirects=False)
        self._log_resp(f"GET /api/user/checkin?month={month}", r, n=260)
        return self._json(r) or {}

    def do_checkin(self):
        r = self._api_post("/api/user/checkin", allow_redirects=False)
        self._log_resp("POST /api/user/checkin", r, n=320)
        return self._json(r) or {"success": False, "message": f"HTTP {r.status_code}"}

    @staticmethod
    def is_checked_in(status_json):
        if not isinstance(status_json, dict):
            return False
        data = status_json.get("data")
        if isinstance(data, dict):
            stats = data.get("stats")
            if isinstance(stats, dict) and stats.get("checked_in_today") is True:
                return True
            if data.get("checked_in_today") is True:
                return True
        msg = str(status_json.get("message") or "")
        return ("已签到" in msg) or ("already" in msg.lower())


def build_msg(uname, result):
    lines = ["✨ 星野公益站签到", f"👤 用户: {uname}"]
    success = bool(result.get("success") is True)
    msg = str(result.get("message") or "")
    if success:
        lines.append(f"✅ 签到成功: {msg or 'OK'}")
    elif "已签到" in msg or "already" in msg.lower():
        lines.append("✅ 今日已签到")
    else:
        lines.append(f"❌ 签到失败: {msg or result}")
    return "\n".join(lines)


def run():
    username = os.environ.get("HOSHINO_LINUXDO_USERNAME") or os.environ.get("CDK_USERNAME") or ""
    password = os.environ.get("HOSHINO_LINUXDO_PASSWORD") or os.environ.get("CDK_PASSWORD") or ""
    username = username.strip()
    password = password.strip()
    if not username or not password:
        return False, "✨ 星野公益站签到\n❌ 缺少 LinuxDo 账号密码（CDK_USERNAME/CDK_PASSWORD）"

    proxy = (os.environ.get("HOSHINO_PROXY") or HARDCODED_PROXY or "").strip() or None
    client = HoshinoClient(proxy=proxy)
    log.info("BASE_URL=%s", BASE_URL)
    log.info("PROXY=%s", proxy)
    log.info("IMPERSONATE=%s", client.impersonate)

    saved = load_session()
    if isinstance(saved, dict):
        sess_cookie = str(saved.get("session_cookie") or "").strip()
        if sess_cookie:
            client.session.cookies.set("session", sess_cookie, domain="api.hoshino.edu.rs", path="/")
        token = str(saved.get("access_token") or "").strip()
        if token:
            client.access_token = token
        uid = saved.get("user_id")
        if isinstance(uid, int):
            client.user_id = uid

        user = client.get_self()
        if user:
            status = client.get_checkin_status()
            if client.is_checked_in(status):
                uname = user.get("username") or user.get("display_name") or user.get("email") or "未知"
                return True, "✨ 星野公益站签到\n✅ 今日已签到\n👤 用户: " + uname
            result = client.do_checkin()
            uname = user.get("username") or user.get("display_name") or user.get("email") or "未知"
            return (bool(result.get("success")) or client.is_checked_in(result)), build_msg(uname, result)

    if not client.login_linuxdo(username, password):
        return False, "✨ 星野公益站签到\n❌ LinuxDo 登录失败"

    delay(1, 2)
    if not client.login_site_by_oauth():
        return False, "✨ 星野公益站签到\n❌ OAuth 登录失败"

    user = client.get_self()
    if not user:
        return False, "✨ 星野公益站签到\n❌ 登录后 /api/user/self 校验失败"

    session_cookie = client.session.cookies.get("session")
    if session_cookie:
        save_session(session_cookie, user_info=user, access_token=client.access_token, user_id=client.user_id)

    status = client.get_checkin_status()
    uname = user.get("username") or user.get("display_name") or user.get("email") or "未知"
    if client.is_checked_in(status):
        return True, "✨ 星野公益站签到\n✅ 今日已签到\n👤 用户: " + uname

    result = client.do_checkin()
    ok = bool(result.get("success")) or client.is_checked_in(result)
    return ok, build_msg(uname, result)


def main():
    load_env()
    os.environ.setdefault("TG_BOT_TOKEN", HARDCODED_TG_BOT_TOKEN)
    os.environ.setdefault("TG_CHAT_ID", HARDCODED_TG_CHAT_ID)

    # Ubuntu 直连：TG 推送默认不走代理；本地调试可设 TG_PROXY 环境变量
    tg_proxy = (os.environ.get("TG_PROXY") or os.environ.get("HOSHINO_PROXY") or "").strip() or None

    ok, msg = run()
    log.info(msg)
    send_telegram(msg, proxy=tg_proxy)

    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()