#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api.codeme.me 自动签到（协议逆向 + 分步测试）

特点：
1) 全程 HTTP 协议级模拟，不使用浏览器自动化
2) 默认直连（Ubuntu 定时任务不走 10808 代理）；本地调试可通过 --proxy 指定
3) 每一步打印状态码/响应头/Cookie/正文片段，便于逆向调试
4) 支持分步执行：analyze / login / checkin / all
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

# 允许从项目根目录导入 utils.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import load_env, send_telegram  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("codeme-checkin")
DEFAULT_LINUXDO_CLIENT_ID = (
    os.environ.get("CODEME_DEFAULT_CLIENT_ID", "ed4CnVPkYpQZSLFdha2pHFtHJOmHQ4bU").strip()
    or "ed4CnVPkYpQZSLFdha2pHFtHJOmHQ4bU"
)


def esc(text, n=500):
    return (text or "")[:n].encode("unicode_escape").decode("ascii")


def deep_find_first_str(obj, key_words):
    """
    在嵌套结构中找第一个满足 key 关键词的字符串值
    key_words: 例如 ("linuxdo", "client")
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


class CodeMeClient:
    def __init__(self, base_url, proxy, impersonate):
        self.base_url = base_url.rstrip("/")
        self.proxy = proxy or None
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

        # token
        token = deep_find_first_str(data, ("token",))
        if not token:
            token = deep_find_first_str(data, ("access", "token"))
        if token and len(token) >= 16:
            self.access_token = token
            log.info("发现 access token（长度=%s）", len(token))

        # user id
        data_obj = data.get("data")
        if isinstance(data_obj, dict) and isinstance(data_obj.get("id"), int):
            self.user_id = data_obj["id"]
            log.info("发现 New-Api-User=%s (from data.id)", self.user_id)
            return

        user_obj = data.get("user")
        if isinstance(user_obj, dict) and isinstance(user_obj.get("id"), int):
            self.user_id = user_obj["id"]
            log.info("发现 New-Api-User=%s (from user.id)", self.user_id)
            return

        uid = data.get("user_id")
        if isinstance(uid, int):
            self.user_id = uid
            log.info("发现 New-Api-User=%s (from user_id)", self.user_id)

    def _log_resp(self, tag, r, n=360):
        headers = dict(r.headers or {})
        log.info("=" * 80)
        log.info("[%s] %s", tag, getattr(r, "url", ""))
        log.info("status=%s content-type=%s", r.status_code, headers.get("content-type"))
        log.info("location=%s", headers.get("location"))
        log.info("cookies=%s", dict(self.session.cookies))
        log.info("body[:%d]=%s", n, esc(r.text, n))
        log.info("=" * 80)

    # ---------- 逆向分析 ----------
    def analyze_home(self):
        r = self._get("/", allow_redirects=False)
        self._log_resp("GET /", r)
        html = r.text or ""

        script_srcs = re.findall(r'<script[^>]+src="([^"]+)"', html, re.I)
        meta_refresh = re.findall(r'http-equiv=["\']refresh["\'][^>]*content=["\']([^"\']+)', html, re.I)
        log.info("首页 script 数量=%d", len(script_srcs))
        if script_srcs:
            log.info("首页 script 示例=%s", script_srcs[:8])
        if meta_refresh:
            log.info("发现 meta refresh=%s", meta_refresh)
        return script_srcs

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
            self._log_resp(f"PROBE {p}", r, n=260)
            out[p] = r
        return out

    def discover_client_id(self, hint=""):
        def _clean_candidates(raw_list):
            uniq = []
            for c in raw_list:
                c = (c or "").strip()
                if not c:
                    continue
                if c.lower() in {"response_type", "client_id", "linuxdo", "authorize", "oauth2"}:
                    continue
                if "/" in c or "http" in c.lower():
                    continue
                if c not in uniq:
                    uniq.append(c)
            return uniq

        if hint:
            log.info("使用外部指定 client_id=%s", hint)
            return hint

        candidates = []

        # 1) 优先从 /api/status JSON 搜（快且稳定）
        try:
            r = self._get("/api/status", allow_redirects=False)
            self._log_resp("DISCOVER /api/status", r, n=320)
            data = self._json(r)

            if isinstance(data, dict):
                direct = deep_find_first_str(data, ("linuxdo", "client"))
                if direct:
                    candidates.append(direct)

                blob = json.dumps(data, ensure_ascii=False)
                for pat in [
                    r'linuxdo[^"\n\r]{0,100}client[^"\n\r]{0,100}["\'=:,\s]+([A-Za-z0-9_-]{6,})',
                    r'"client_id"\s*:\s*"([A-Za-z0-9_-]{6,})"',
                ]:
                    candidates.extend(re.findall(pat, blob, flags=re.I))

            uniq = _clean_candidates(candidates)
            if uniq:
                log.info("client_id 候选: %s", uniq[:10])
                return uniq[0]
        except Exception as e:
            log.warning("从 /api/status 探测 client_id 失败: %s", e)

        # 2) 回退从首页主 bundle 搜（可能较慢，逐个容错）
        try:
            home = self._get("/", allow_redirects=False)
            html = home.text or ""
            script_srcs = re.findall(r'<script[^>]+src="([^"]*index-[^"]+\.js)"', html, re.I)
        except Exception as e:
            log.warning("读取首页失败，跳过 bundle 探测: %s", e)
            script_srcs = []

        for src in script_srcs[:3]:
            try:
                url = urljoin(self.base_url + "/", src)
                jr = self._get(url, allow_redirects=False)
                self._log_resp(f"DISCOVER JS {src}", jr, n=240)
                js = jr.text or ""

                for pat in [
                    r'linuxdo[^"\n\r]{0,120}client[^"\n\r]{0,120}["\'=:,\s]+([A-Za-z0-9_-]{6,})',
                    r'client_id["\']?\s*[:=]\s*["\']([A-Za-z0-9_-]{6,})["\']',
                ]:
                    candidates.extend(re.findall(pat, js, flags=re.I))
            except Exception as e:
                log.warning("探测 JS %s 失败: %s", src, e)

        uniq = _clean_candidates(candidates)
        if uniq:
            log.info("client_id 候选: %s", uniq[:10])
            return uniq[0]

        # 3) 最终兜底：使用默认值，避免因探测超时导致任务整体失败
        log.warning("未自动发现 client_id，使用默认 client_id=%s", DEFAULT_LINUXDO_CLIENT_ID)
        return DEFAULT_LINUXDO_CLIENT_ID

    # ---------- 认证流程 ----------
    def login_linuxdo(self, username, password):
        if not username or not password:
            log.error("缺少 LinuxDo 账号密码")
            return False

        r = self.session.get(
            "https://linux.do/session/csrf.json",
            proxy=self.proxy,
            timeout=30,
            allow_redirects=False,
        )
        self._log_resp("linux.do csrf", r, n=220)
        if r.status_code != 200:
            return False

        data = self._json(r)
        csrf = data.get("csrf") if isinstance(data, dict) else ""
        if not csrf:
            return False

        lr = self.session.post(
            "https://linux.do/session",
            proxy=self.proxy,
            timeout=30,
            allow_redirects=False,
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
        self._log_resp("linux.do login", lr, n=320)
        j = self._json(lr)
        if isinstance(j, dict) and j.get("user", {}).get("username"):
            log.info("LinuxDo 登录成功: %s", j.get("user", {}).get("username"))
            return True

        log.error("LinuxDo 登录失败: %s", j if isinstance(j, dict) else lr.text[:120])
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
            r = self.session.get(
                current,
                proxy=self.proxy,
                timeout=30,
                allow_redirects=False,
            )
            loc = r.headers.get("location", "")
            history.append((current, r.status_code, loc))
            log.info("hop#%d status=%s %s -> %s", i + 1, r.status_code, current, loc)

            last = r

            # 连接页可能要求手动 approve
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
        state_r = self._get("/api/oauth/state", allow_redirects=False)
        self._log_resp("GET /api/oauth/state", state_r, n=260)
        state_j = self._json(state_r)
        state = ""
        if isinstance(state_j, dict) and state_j.get("success"):
            state = str(state_j.get("data") or "").strip()
        if not state:
            log.error("获取 state 失败")
            return False

        if not client_id:
            log.error("client_id 为空，无法发起 OAuth")
            return False

        authorize_url = (
            "https://connect.linux.do/oauth2/authorize?"
            + urlencode({"response_type": "code", "client_id": client_id, "state": state})
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

        if not code:
            log.error("未提取到 OAuth code")
            return False

        cb = "/api/oauth/linuxdo?" + urlencode({"code": code, "state": state})
        cr = self._get(cb, allow_redirects=False)
        self._log_resp("GET /api/oauth/linuxdo callback", cr, n=360)
        j = self._json(cr)
        self._update_auth_from_json(j)

        user = self.get_self()
        if user:
            uname = user.get("username") or user.get("display_name") or user.get("email") or "unknown"
            log.info("OAuth 登录成功: %s", uname)
            return True

        # 兼容某些站点：虽然 callback 返回异常，但 cookie/token 已写入，再补一次 self
        user2 = self.get_self()
        if user2:
            return True

        log.error("OAuth 回调后 /api/user/self 仍不可用")
        return False

    # ---------- 用户与签到 ----------
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
        self._log_resp(f"GET /api/user/checkin?month={month}", r, n=280)
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


def run(mode="all", client_id_hint="", base_url="https://api.codeme.me", proxy=None, impersonate="chrome136"):
    linuxdo_username = (
        os.environ.get("CODEME_LINUXDO_USERNAME")
        or os.environ.get("CDK_USERNAME")
        or ""
    ).strip()
    linuxdo_password = (
        os.environ.get("CODEME_LINUXDO_PASSWORD")
        or os.environ.get("CDK_PASSWORD")
        or ""
    ).strip()

    client = CodeMeClient(base_url=base_url, proxy=proxy, impersonate=impersonate)

    log.info("BASE_URL=%s", base_url)
    log.info("PROXY=%s", proxy)
    log.info("IMPERSONATE=%s", impersonate)
    log.info("MODE=%s", mode)

    # Step A: 页面 + API 逆向
    client.analyze_home()
    client.probe_common_apis()
    client_id = client.discover_client_id(client_id_hint)
    log.info("最终使用 client_id=%s", client_id or "(空)")

    if mode == "analyze":
        return True, f"太子公益站逆向分析完成\nclient_id={client_id or '未发现'}"

    # Step B: LinuxDo 登录 + OAuth 回调
    if not client.login_linuxdo(linuxdo_username, linuxdo_password):
        return False, "太子公益站签到\n❌ LinuxDo 登录失败"

    if not client.oauth_login(client_id):
        return False, "太子公益站签到\n❌ OAuth 登录失败（可能是 client_id 未识别）"

    user = client.get_self()
    if not user:
        return False, "太子公益站签到\n❌ 登录后 /api/user/self 校验失败"

    uname = user.get("username") or user.get("display_name") or user.get("email") or "未知"

    if mode == "login":
        return True, f"太子公益站签到\n✅ 登录成功\n👤 用户: {uname}"

    # Step C: 签到流程
    status = client.get_checkin_status()
    if client.is_checked_in(status):
        return True, f"太子公益站签到\n✅ 今日已签到\n👤 用户: {uname}"

    result = client.do_checkin()
    success = bool(result.get("success") is True)
    msg = str(result.get("message") or "")
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    quota_awarded = data.get("quota_awarded")

    if success:
        extra = f"\n🎁 奖励: {quota_awarded}" if quota_awarded else ""
        return True, f"太子公益站签到\n✅ 签到成功\n👤 用户: {uname}{extra}"

    if "已签到" in msg or "already" in msg.lower():
        return True, f"太子公益站签到\n✅ 今日已签到\n👤 用户: {uname}"

    return False, f"太子公益站签到\n❌ 签到失败: {msg or result}"


def parse_bool_env(name, default=True):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "y"}


def parse_args():
    parser = argparse.ArgumentParser(description="api.codeme.me 协议逆向 + 自动签到")
    parser.add_argument("--mode", choices=["analyze", "login", "checkin", "all"], default="all")
    parser.add_argument("--base-url", default=os.environ.get("CODEME_BASE_URL", "https://api.codeme.me"))
    parser.add_argument("--proxy", default=os.environ.get("CODEME_PROXY", ""))
    parser.add_argument("--no-proxy", action="store_true", help="不使用代理")
    parser.add_argument("--impersonate", default=os.environ.get("CODEME_IMPERSONATE", "chrome136"))
    parser.add_argument("--client-id", default=os.environ.get("CODEME_LINUXDO_CLIENT_ID", ""))
    parser.add_argument("--no-tg", action="store_true", help="禁用 Telegram 推送")
    parser.add_argument(
        "--tg-on-success",
        action="store_true",
        default=parse_bool_env("CODEME_TG_ON_SUCCESS", True),
        help="成功时也推送 Telegram（默认开启，可用环境变量 CODEME_TG_ON_SUCCESS 控制）",
    )
    return parser.parse_args()


def main():
    load_env()
    args = parse_args()

    proxy = None if args.no_proxy else (args.proxy.strip() or None)
    mode = "all" if args.mode == "checkin" else args.mode  # checkin 与 all 等效（包含前置步骤）

    ok, msg = run(
        mode=mode,
        client_id_hint=(args.client_id or "").strip(),
        base_url=args.base_url.strip().rstrip("/"),
        proxy=proxy,
        impersonate=(args.impersonate or "chrome136").strip(),
    )

    log.info(msg)

    if not args.no_tg and (args.tg_on_success or (not ok)):
        send_telegram(msg)

    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()