#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
星野公益站 (api.hoshino.edu.rs) 自动签到（协议级逆向）

说明：
1) 全程 HTTP 请求模拟，不使用浏览器自动化
2) 默认走本地代理 http://127.0.0.1:10808（可 --no-proxy）
3) 分步日志：页面分析 -> API 探测 -> OAuth 登录 -> 签到
"""

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from curl_cffi import requests

# 允许导入 linuxdo/utils.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import load_env, send_telegram  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("hoshino-checkin")

BASE_URL_DEFAULT = "https://api.hoshino.edu.rs"
ENTRY_PATH_DEFAULT = "/pricing"
DEFAULT_PROXY = os.environ.get("HOSHINO_PROXY", "http://127.0.0.1:10808").strip()
DEFAULT_IMPERSONATE = os.environ.get("HOSHINO_IMPERSONATE", "chrome136").strip() or "chrome136"
DEFAULT_CLIENT_ID = os.environ.get("HOSHINO_LINUXDO_CLIENT_ID", "").strip()
DEFAULT_CONNECT_COOKIE = os.environ.get(
    "HOSHINO_CONNECT_COOKIE",
    os.environ.get("LINUXDO_CONNECT_COOKIE", ""),
).strip()
IMPERSONATE_TARGETS = ["chrome133a", "chrome136", "chrome142"]


def esc(text, n=500):
    return (text or "")[:n].encode("unicode_escape").decode("ascii")


def parse_bool_env(name, default=True):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "y"}


def deep_find_first_str(obj, key_words):
    """
    在嵌套对象里查找 key 包含所有关键词的字符串值。
    key_words: ("linuxdo", "client")
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = str(k).lower()
            if all(w in lk for w in key_words) and isinstance(v, str) and v.strip():
                return v.strip()
        for v in obj.values():
            found = deep_find_first_str(v, key_words)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = deep_find_first_str(item, key_words)
            if found:
                return found
    return ""


class HoshinoClient:
    def __init__(self, base_url, entry_path, proxy, impersonate):
        self.base_url = base_url.rstrip("/")
        self.entry_path = entry_path if entry_path.startswith("/") else f"/{entry_path}"
        self.proxy = proxy or None
        self.impersonate = impersonate
        self.session = requests.Session(impersonate=impersonate)
        self.access_token = ""
        self.user_id = None

    def _url(self, path):
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    def _get(self, path, **kwargs):
        kwargs.setdefault("proxy", self.proxy)
        kwargs.setdefault("timeout", 30)
        return self.session.get(self._url(path), **kwargs)

    def _post(self, path, **kwargs):
        kwargs.setdefault("proxy", self.proxy)
        kwargs.setdefault("timeout", 30)
        return self.session.post(self._url(path), **kwargs)

    def _json(self, r):
        try:
            return r.json()
        except Exception:
            return None

    def _switch_impersonate(self, target):
        """切换 TLS 指纹并尽量保留现有 cookies"""
        old = []
        jar = self.session.cookies
        try:
            for c in jar.jar:
                old.append((c.name, c.value, getattr(c, "domain", None), getattr(c, "path", "/")))
        except Exception:
            if hasattr(jar, "items"):
                for k, v in jar.items():
                    old.append((k, v, None, "/"))

        self.impersonate = target
        self.session = requests.Session(impersonate=target)

        for name, value, domain, path in old:
            kwargs = {}
            if domain:
                kwargs["domain"] = domain
            if path:
                kwargs["path"] = path
            self.session.cookies.set(name, value, **kwargs)

    def set_cookie_string(self, cookie_str, domains=None):
        if not cookie_str:
            return
        if not domains:
            domains = ("connect.linux.do",)

        for part in cookie_str.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            name, value = part.split("=", 1)
            name = name.strip()
            value = value.strip()
            if not name:
                continue
            for d in domains:
                self.session.cookies.set(name, value, domain=d, path="/")

    def _auth_headers(self, headers=None):
        h = dict(headers or {})
        if self.access_token:
            h["Authorization"] = f"Bearer {self.access_token}"
        if self.user_id is not None:
            h["New-Api-User"] = str(self.user_id)
        return h

    def _update_auth_from_json(self, data):
        if not isinstance(data, dict):
            return

        token_keys = ("token", "access_token", "accessToken", "jwt", "bearer")
        for k in token_keys:
            v = data.get(k)
            if isinstance(v, str) and len(v) >= 16:
                self.access_token = v
                log.info("发现 access token（key=%s, len=%s）", k, len(v))
                break

        data_obj = data.get("data")
        if isinstance(data_obj, dict) and isinstance(data_obj.get("id"), int):
            self.user_id = data_obj["id"]
        user_obj = data.get("user")
        if self.user_id is None and isinstance(user_obj, dict) and isinstance(user_obj.get("id"), int):
            self.user_id = user_obj["id"]
        uid = data.get("user_id")
        if self.user_id is None and isinstance(uid, int):
            self.user_id = uid

        if self.user_id is not None:
            log.info("发现 New-Api-User=%s", self.user_id)

    def _log_resp(self, tag, r, n=420):
        headers = dict(r.headers or {})
        log.info("=" * 80)
        log.info("[%s] %s", tag, getattr(r, "url", ""))
        log.info("status=%s content-type=%s", r.status_code, headers.get("content-type"))
        log.info("location=%s", headers.get("location"))
        log.info("cookies=%s", dict(self.session.cookies))
        log.info("body[:%d]=%s", n, esc(r.text, n))
        log.info("=" * 80)

    # ---------- 第一步：页面分析 ----------
    def analyze_entry_page(self):
        r = self._get(self.entry_path, allow_redirects=False)
        self._log_resp(f"GET {self.entry_path}", r, n=700)
        html = r.text or ""

        scripts = re.findall(r'<script[^>]+src="([^"]+)"', html, re.I)
        metas = re.findall(r'http-equiv=["\']refresh["\'][^>]*content=["\']([^"\']+)', html, re.I)
        forms = re.findall(r"<form[^>]*>", html, re.I)

        log.info("页面脚本数量=%d", len(scripts))
        log.info("页面 form 数量=%d", len(forms))
        if scripts:
            log.info("脚本示例=%s", scripts[:8])
        if metas:
            log.info("meta refresh=%s", metas)

        return scripts

    # ---------- 第二步：API 探测 ----------
    def probe_common_apis(self):
        paths = [
            "/api/status",
            "/api/notice",
            "/api/oauth/state",
            "/api/oauth/linuxdo",
            "/api/user/self",
            "/api/user/checkin",
            "/api/user/login",
            "/api/system/status",
            "/api/option/linuxdo_client_id",
            "/api/option/linuxdo.client_id",
        ]
        out = {}
        for p in paths:
            r = self._get(p, allow_redirects=False)
            self._log_resp(f"PROBE {p}", r, n=280)
            out[p] = r
        return out

    def discover_client_id(self, hint=""):
        if hint:
            log.info("使用传入 client_id=%s", hint)
            return hint

        candidates = []

        # 1) 从 JSON API 探测
        for ep in ["/api/status", "/api/system/status", "/api/notice", "/api/option/linuxdo_client_id", "/api/option/linuxdo.client_id"]:
            try:
                r = self._get(ep, allow_redirects=False)
                data = self._json(r)
                if isinstance(data, dict):
                    direct = (
                        data.get("linuxdo_client_id")
                        or data.get("client_id")
                        or deep_find_first_str(data, ("linuxdo", "client"))
                    )
                    if isinstance(direct, str) and direct.strip():
                        candidates.append(direct.strip())
                    blob = json.dumps(data, ensure_ascii=False)
                    candidates.extend(re.findall(r'client_id["\']?\s*[:=]\s*["\']([A-Za-z0-9_-]{6,})["\']', blob, re.I))
                    candidates.extend(re.findall(r'linuxdo[^"\n\r]{0,120}client[^"\n\r]{0,120}["\'=:,\s]+([A-Za-z0-9_-]{6,})', blob, re.I))
            except Exception as e:
                log.warning("探测 %s 异常: %s", ep, e)

        # 2) 从入口页 script bundle 探测
        script_urls = []
        try:
            scripts = self.analyze_entry_page()
            for src in scripts:
                script_urls.append(urljoin(self.base_url + "/", src))
        except Exception as e:
            log.warning("入口页脚本提取异常: %s", e)

        for js_url in script_urls[:6]:
            try:
                jr = self._get(js_url, allow_redirects=False)
                if jr.status_code != 200:
                    continue
                txt = jr.text or ""
                candidates.extend(re.findall(r'client_id["\']?\s*[:=]\s*["\']([A-Za-z0-9_-]{6,})["\']', txt, re.I))
                candidates.extend(re.findall(r'connect\.linux\.do/oauth2/authorize\?[^"\']*client_id=([A-Za-z0-9_-]{6,})', txt, re.I))
                candidates.extend(re.findall(r'linuxdo[^"\n\r]{0,120}client[^"\n\r]{0,120}["\'=:,\s]+([A-Za-z0-9_-]{6,})', txt, re.I))
            except Exception as e:
                log.warning("探测 JS %s 异常: %s", js_url, e)

        # 去重 + 清洗
        uniq = []
        for c in candidates:
            c = (c or "").strip()
            if not c:
                continue
            if c.lower() in {"client_id", "response_type", "linuxdo", "authorize", "oauth2"}:
                continue
            if "/" in c or "http" in c.lower():
                continue
            if c not in uniq:
                uniq.append(c)

        if uniq:
            log.info("client_id 候选=%s", uniq[:10])
            return uniq[0]

        log.warning("未自动发现 client_id，请通过 --client-id 或环境变量 HOSHINO_LINUXDO_CLIENT_ID 提供")
        return ""

    # ---------- 第三步：认证流程 ----------
    def _get_oauth_state(self):
        sr = self._get("/api/oauth/state", allow_redirects=False)
        self._log_resp("GET /api/oauth/state", sr, n=280)
        sj = self._json(sr)
        state = ""
        if isinstance(sj, dict) and sj.get("success"):
            state = str(sj.get("data") or "").strip()
        if not state:
            log.error("获取 state 失败")
            return ""
        log.info("获取到 OAuth state=%s", state)
        return state

    def login_linuxdo(self, connect_cookie=""):
        """注入 connect.linux.do cookie，走 OAuth 授权码回调流程建立星野站会话。"""
        if not connect_cookie:
            log.error("缺少 connect.linux.do Cookie（HOSHINO_CONNECT_COOKIE / --connect-cookie）")
            return False

        log.info("注入 connect.linux.do Cookie")
        self.set_cookie_string(connect_cookie, domains=("connect.linux.do",))

        state = self._get_oauth_state()
        if not state:
            return False

        rr = self._get(
            "/api/oauth/linuxdo",
            params={"state": state},
            allow_redirects=True,
        )
        self._log_resp("GET /api/oauth/linuxdo?state=... (follow redirects)", rr, n=420)

        try:
            hist = getattr(rr, "history", []) or []
            for i, hr in enumerate(hist, start=1):
                log.info("oauth history#%d status=%s url=%s", i, hr.status_code, hr.url)
        except Exception:
            pass

        user = self.get_self()
        if user:
            log.info("OAuth 登录成功: %s", user.get("username") or user.get("display_name") or "unknown")
            return True

        log.error("OAuth 回调后 /api/user/self 不可用")
        return False

    def _extract_code_from_url(self, u):
        try:
            q = parse_qs(urlparse(u).query)
            if "code" in q and q["code"]:
                return q["code"][0]
        except Exception:
            pass
        return ""

    def _walk_redirects(self, start_url, max_hops=30):
        history = []
        current = start_url
        last = None
        for i in range(max_hops):
            r = self.session.get(current, proxy=self.proxy, timeout=30, allow_redirects=False)
            loc = r.headers.get("location", "")
            history.append((current, r.status_code, loc))
            log.info("hop#%d status=%s %s -> %s", i + 1, r.status_code, current, loc)
            last = r

            if r.status_code == 200 and "connect.linux.do" in current:
                approve = re.search(r'href="(/oauth2/approve/[^"]+)"', r.text or "")
                if approve:
                    current = urljoin(current, approve.group(1))
                    log.info("发现授权链接，继续访问: %s", current)
                    continue

            if r.status_code in (301, 302, 303, 307, 308) and loc:
                current = urljoin(current, loc)
                continue
            break
        return current, last, history

    def oauth_login(self, client_id):
        sr = self._get("/api/oauth/state", allow_redirects=False)
        self._log_resp("GET /api/oauth/state", sr, n=280)
        sj = self._json(sr)
        state = ""
        if isinstance(sj, dict) and sj.get("success"):
            state = str(sj.get("data") or "").strip()
        if not state:
            log.error("获取 state 失败")
            return False
        if not client_id:
            log.error("client_id 为空")
            return False

        authorize_url = "https://connect.linux.do/oauth2/authorize?" + urlencode(
            {"response_type": "code", "client_id": client_id, "state": state}
        )
        log.info("authorize_url=%s", authorize_url)

        final_url, final_r, history = self._walk_redirects(authorize_url)
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

        if not code and final_r is not None and final_r.status_code == 200:
            m = re.search(r"[?&]code=([^&\"'>]+)", final_r.text or "")
            if m:
                code = m.group(1)

        if not code:
            log.error("OAuth 未提取到 code")
            return False

        cb = "/api/oauth/linuxdo?" + urlencode({"code": code, "state": state})
        cr = self._get(cb, allow_redirects=False)
        self._log_resp("GET /api/oauth/linuxdo callback", cr, n=380)
        j = self._json(cr)
        self._update_auth_from_json(j)

        user = self.get_self()
        if user:
            log.info("OAuth 登录成功: %s", user.get("username") or user.get("display_name") or "unknown")
            return True

        log.error("OAuth 回调后 /api/user/self 不可用")
        return False

    # ---------- 第四步：签到 ----------
    def get_self(self):
        r = self._get("/api/user/self", headers=self._auth_headers(), allow_redirects=False)
        self._log_resp("GET /api/user/self", r, n=260)
        j = self._json(r)
        self._update_auth_from_json(j)
        if isinstance(j, dict) and j.get("success") is True and isinstance(j.get("data"), dict):
            return j["data"]
        return None

    def get_checkin_status(self, month=None):
        if not month:
            month = datetime.now().strftime("%Y-%m")
        r = self._get(
            "/api/user/checkin",
            headers=self._auth_headers(),
            params={"month": month},
            allow_redirects=False,
        )
        self._log_resp(f"GET /api/user/checkin?month={month}", r, n=300)
        return self._json(r) or {}

    def do_checkin(self):
        r = self._post("/api/user/checkin", headers=self._auth_headers(), allow_redirects=False)
        self._log_resp("POST /api/user/checkin", r, n=320)
        return self._json(r) or {}

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
        if "已签到" in msg or "already" in msg.lower():
            return True
        return False


def run(
    mode="all",
    base_url=BASE_URL_DEFAULT,
    entry_path=ENTRY_PATH_DEFAULT,
    client_id_hint="",
    proxy=DEFAULT_PROXY,
    impersonate=DEFAULT_IMPERSONATE,
    connect_cookie="",
):
    if not connect_cookie:
        connect_cookie = (
            os.environ.get("HOSHINO_CONNECT_COOKIE")
            or os.environ.get("LINUXDO_CONNECT_COOKIE")
            or DEFAULT_CONNECT_COOKIE
            or ""
        ).strip()

    client = HoshinoClient(base_url=base_url, entry_path=entry_path, proxy=proxy, impersonate=impersonate)

    log.info("BASE_URL=%s", base_url)
    log.info("ENTRY_PATH=%s", entry_path)
    log.info("PROXY=%s", proxy)
    log.info("IMPERSONATE=%s", impersonate)
    log.info("MODE=%s", mode)

    # A. 页面与接口逆向探测
    client.analyze_entry_page()
    client.probe_common_apis()
    client_id = client.discover_client_id(client_id_hint or DEFAULT_CLIENT_ID)
    log.info("最终使用 client_id=%s", client_id or "(空)")

    if mode == "analyze":
        return True, f"星野公益站逆向分析完成\nclient_id={client_id or '未发现'}"

    # B. connect.linux.do OAuth 登录
    if not client.login_linuxdo(connect_cookie=connect_cookie):
        return False, "星野公益站签到\n❌ connect.linux.do OAuth 登录失败（请设置 HOSHINO_CONNECT_COOKIE 或 --connect-cookie）"

    user = client.get_self()
    if not user:
        return False, "星野公益站签到\n❌ 登录后 /api/user/self 校验失败"

    uname = user.get("username") or user.get("display_name") or user.get("email") or "未知"

    if mode == "login":
        return True, f"星野公益站签到\n✅ 登录成功\n👤 用户: {uname}"

    # C. 签到
    status = client.get_checkin_status()
    if client.is_checked_in(status):
        return True, f"星野公益站签到\n✅ 今日已签到\n👤 用户: {uname}"

    result = client.do_checkin()
    success = bool(result.get("success") is True)
    msg = str(result.get("message") or "")
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    quota_awarded = data.get("quota_awarded")

    if success:
        extra = f"\n🎁 奖励: {quota_awarded}" if quota_awarded else ""
        return True, f"星野公益站签到\n✅ 签到成功\n👤 用户: {uname}{extra}"

    if "已签到" in msg or "already" in msg.lower():
        return True, f"星野公益站签到\n✅ 今日已签到\n👤 用户: {uname}"

    return False, f"星野公益站签到\n❌ 签到失败: {msg or result}"


def parse_args():
    parser = argparse.ArgumentParser(description="api.hoshino.edu.rs 协议逆向 + 自动签到")
    parser.add_argument("--mode", choices=["analyze", "login", "checkin", "all"], default="all")
    parser.add_argument("--base-url", default=os.environ.get("HOSHINO_BASE_URL", BASE_URL_DEFAULT))
    parser.add_argument("--entry-path", default=os.environ.get("HOSHINO_ENTRY_PATH", ENTRY_PATH_DEFAULT))
    parser.add_argument("--proxy", default=os.environ.get("HOSHINO_PROXY", DEFAULT_PROXY))
    parser.add_argument("--no-proxy", action="store_true", help="不使用代理")
    parser.add_argument("--impersonate", default=os.environ.get("HOSHINO_IMPERSONATE", DEFAULT_IMPERSONATE))
    parser.add_argument("--client-id", default=os.environ.get("HOSHINO_LINUXDO_CLIENT_ID", DEFAULT_CLIENT_ID))
    parser.add_argument(
        "--connect-cookie",
        default=os.environ.get("HOSHINO_CONNECT_COOKIE", DEFAULT_CONNECT_COOKIE),
        help="必填：connect.linux.do 的 Cookie（可用环境变量 HOSHINO_CONNECT_COOKIE）",
    )
    parser.add_argument("--no-tg", action="store_true", help="禁用 Telegram 推送")
    parser.add_argument(
        "--tg-on-success",
        action="store_true",
        default=parse_bool_env("HOSHINO_TG_ON_SUCCESS", True),
        help="成功时也推送 Telegram（默认开启）",
    )
    return parser.parse_args()


def main():
    load_env()
    args = parse_args()

    proxy = None if args.no_proxy else ((args.proxy or "").strip() or None)
    mode = "all" if args.mode == "checkin" else args.mode

    ok, msg = run(
        mode=mode,
        base_url=(args.base_url or BASE_URL_DEFAULT).strip().rstrip("/"),
        entry_path=(args.entry_path or ENTRY_PATH_DEFAULT).strip(),
        client_id_hint=(args.client_id or "").strip(),
        proxy=proxy,
        impersonate=(args.impersonate or DEFAULT_IMPERSONATE).strip(),
        connect_cookie=(args.connect_cookie or "").strip(),
    )

    log.info(msg)
    if not args.no_tg and (args.tg_on_success or (not ok)):
        send_telegram(msg)

    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()