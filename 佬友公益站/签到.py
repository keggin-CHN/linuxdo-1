#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Freestyle(new-api) 自动签到脚本（协议级）
目标站点: https://api.freestyle.cc.cd/console/personal

特性：
1) 全流程默认走本地代理 10808
2) 支持两种登录模式：
   - account: 站点账号密码登录 (/api/user/login)
   - linuxdo: 通过 LinuxDo OAuth 回调 (/api/oauth/state -> connect.linux.do -> /api/oauth/linuxdo)
3) 详细日志，便于逆向调试
"""

import os
import re
import json
import logging
from datetime import datetime
from urllib.parse import urljoin, urlencode, urlparse, parse_qs

from curl_cffi import requests

from utils import load_env, send_telegram


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("freestyle-checkin")


# ===== 写死配置（单用户）=====
HARDCODED_CDK_USERNAME = "zhou239289001@gmail.com"
HARDCODED_CDK_PASSWORD = "zhou060423rls"
HARDCODED_FREESTYLE_USERNAME = ""
HARDCODED_FREESTYLE_PASSWORD = ""
HARDCODED_TG_BOT_TOKEN = "7483346980:AAHT4LBRiDU0H617sRQZmNUL8A6GumybMHE"
HARDCODED_TG_CHAT_ID = "7420206850"
HARDCODED_PROXY = "http://127.0.0.1:10808"

BASE_URL = os.environ.get("FREESTYLE_BASE_URL", "https://api.freestyle.cc.cd").rstrip("/")
PROXY = os.environ.get("FREESTYLE_PROXY", "").strip() or HARDCODED_PROXY
IMPERSONATE = os.environ.get("FREESTYLE_IMPERSONATE", "chrome136").strip()

# 默认 LinuxDo OAuth（仍允许环境变量覆盖）
LOGIN_MODE = os.environ.get("FREESTYLE_LOGIN_MODE", "").strip().lower() or "linuxdo"

FREESTYLE_USERNAME = os.environ.get("FREESTYLE_USERNAME", "").strip() or HARDCODED_FREESTYLE_USERNAME
FREESTYLE_PASSWORD = os.environ.get("FREESTYLE_PASSWORD", "").strip() or HARDCODED_FREESTYLE_PASSWORD

LINUXDO_USERNAME = (
    os.environ.get("FREESTYLE_LINUXDO_USERNAME")
    or os.environ.get("CDK_USERNAME")
    or HARDCODED_CDK_USERNAME
    or ""
).strip()
LINUXDO_PASSWORD = (
    os.environ.get("FREESTYLE_LINUXDO_PASSWORD")
    or os.environ.get("CDK_PASSWORD")
    or HARDCODED_CDK_PASSWORD
    or ""
).strip()
LINUXDO_CLIENT_ID = os.environ.get("FREESTYLE_LINUXDO_CLIENT_ID", "").strip()

# 单用户固定：成功/失败都推送
TG_ON_SUCCESS = True


def esc(text, n=500):
    return (text or "")[:n].encode("unicode_escape").decode("ascii")


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


class FreestyleClient:
    def __init__(self, base_url=BASE_URL, proxy=PROXY, impersonate=IMPERSONATE):
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

    def _auth_headers(self, headers=None):
        h = dict(headers or {})
        if self.access_token:
            h["Authorization"] = f"Bearer {self.access_token}"
        if self.user_id is not None:
            h["New-Api-User"] = str(self.user_id)
        return h

    def _log_resp(self, tag, r, n=420):
        log.info("=" * 78)
        log.info("[%s] %s", tag, getattr(r, "url", ""))
        log.info("status=%s ct=%s", r.status_code, r.headers.get("content-type"))
        log.info("location=%s", r.headers.get("location"))
        log.info("cookies=%s", dict(self.session.cookies))
        log.info("body[:%d]=%s", n, esc(r.text, n))
        log.info("=" * 78)

    def _json(self, r):
        try:
            return r.json()
        except Exception:
            return None

    def _update_token_from_json(self, data):
        if not isinstance(data, dict):
            return
        token = deep_find_first_str(
            data,
            {"token", "access_token", "accesstoken", "jwt", "bearer"},
        )
        if token:
            self.access_token = token
            log.info("发现 access token（长度=%d）", len(token))

    def _update_user_id_from_json(self, data):
        if not isinstance(data, dict):
            return

        # 常见结构：{"data":{"id":4578,...}}
        data_obj = data.get("data")
        if isinstance(data_obj, dict) and isinstance(data_obj.get("id"), int):
            self.user_id = data_obj["id"]
            log.info("发现 New-Api-User=%s (from data.id)", self.user_id)
            return

        # 次常见结构：{"user":{"id":...}}
        user_obj = data.get("user")
        if isinstance(user_obj, dict) and isinstance(user_obj.get("id"), int):
            self.user_id = user_obj["id"]
            log.info("发现 New-Api-User=%s (from user.id)", self.user_id)
            return

        # 兜底：尝试扫描 user_id
        uid = data.get("user_id")
        if isinstance(uid, int):
            self.user_id = uid
            log.info("发现 New-Api-User=%s (from user_id)", self.user_id)

    # ---------- 协议探测 ----------
    def get_oauth_state(self):
        r = self._get("/api/oauth/state", allow_redirects=False)
        self._log_resp("GET /api/oauth/state", r)
        data = self._json(r)
        if isinstance(data, dict) and data.get("success"):
            state = str(data.get("data") or "").strip()
            return state
        return ""

    def discover_linuxdo_client_id(self):
        """
        自动探测 LinuxDo client_id
        """
        candidates = [
            "/api/option/linuxdo_client_id",
            "/api/option/linuxdo.client_id",
            "/api/option/auth_linuxdo_client_id",
            "/api/option/oauth_linuxdo_client_id",
            "/api/status",
            "/api/system/status",
            "/api/notice",
            "/api/user/oauth/bindings",
        ]

        key_regex = re.compile(
            r"""linuxdo[^"'\n\r:]{0,40}client[^"'\n\r:]{0,40}["'\s:=]+([A-Za-z0-9_-]{6,})""",
            re.I,
        )

        for ep in candidates:
            try:
                r = self._get(ep, allow_redirects=False)
                self._log_resp(f"DISCOVER {ep}", r, n=320)

                data = self._json(r)
                if isinstance(data, dict):
                    # 1) 直接常见键名
                    direct = (
                        data.get("linuxdo_client_id")
                        or data.get("client_id")
                        or deep_find_first_str(data, {"linuxdo_client_id", "client_id"})
                    )
                    if isinstance(direct, str) and direct.strip():
                        log.info("探测到 client_id=%s (from %s)", direct.strip(), ep)
                        return direct.strip()

                    # 2) json 序列化后 regex 搜
                    blob = json.dumps(data, ensure_ascii=False)
                    m = key_regex.search(blob)
                    if m:
                        cid = m.group(1).strip()
                        log.info("探测到 client_id=%s (regex from %s)", cid, ep)
                        return cid

                # 3) 文本 regex 搜
                m2 = key_regex.search(r.text or "")
                if m2:
                    cid = m2.group(1).strip()
                    log.info("探测到 client_id=%s (text from %s)", cid, ep)
                    return cid
            except Exception as e:
                log.warning("探测 %s 异常: %s", ep, e)

        log.warning("自动探测 client_id 失败，请设置 FREESTYLE_LINUXDO_CLIENT_ID")
        return ""

    # ---------- 登录：站点账号 ----------
    def login_account(self, username, password):
        if not username or not password:
            log.error("FREESTYLE_USERNAME / FREESTYLE_PASSWORD 未设置")
            return False

        payloads = [
            {"username": username, "password": password},
            {"email": username, "password": password},
            {"account": username, "password": password},
            {"login": username, "password": password},
        ]

        tried = 0
        for p in payloads:
            tried += 1
            r = self._post("/api/user/login", json=p, allow_redirects=False)
            self._log_resp(f"POST /api/user/login json variant#{tried}", r)

            data = self._json(r)
            if isinstance(data, dict):
                self._update_token_from_json(data)
                self._update_user_id_from_json(data)
                if data.get("success") is True:
                    log.info("账号密码登录成功（json variant %d）", tried)
                    return True
                msg = str(data.get("message") or "")
                # 某些场景返回 need-2fa / turnstile
                if "2fa" in msg.lower():
                    log.error("账号开启 2FA，当前脚本暂不自动处理 2FA")
                    return False

            # 再尝试 form
            r2 = self._post("/api/user/login", data=p, allow_redirects=False)
            self._log_resp(f"POST /api/user/login form variant#{tried}", r2)
            data2 = self._json(r2)
            if isinstance(data2, dict):
                self._update_token_from_json(data2)
                self._update_user_id_from_json(data2)
                if data2.get("success") is True:
                    log.info("账号密码登录成功（form variant %d）", tried)
                    return True

        log.error("账号密码登录失败")
        return False

    # ---------- 登录：LinuxDo OAuth ----------
    def _linuxdo_login(self, username, password):
        r = self.session.get(
            "https://linux.do/session/csrf.json",
            proxy=self.proxy,
            timeout=30,
            allow_redirects=False,
        )
        self._log_resp("linux.do csrf", r, n=260)
        if r.status_code != 200:
            return False

        data = self._json(r)
        csrf = (data.get("csrf") if isinstance(data, dict) else "") or ""
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
        self._log_resp("linux.do login", lr, n=380)

        j = self._json(lr)
        if isinstance(j, dict) and j.get("user", {}).get("username"):
            return True

        err = j.get("error") if isinstance(j, dict) else ""
        if err:
            log.error("linux.do 登录失败: %s", err)
        return False

    def _walk_redirects(self, start_url, max_hops=25):
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
            history.append((current, r.status_code, r.headers.get("location", "")))
            last = r
            loc = r.headers.get("location", "")

            log.info("hop#%d status=%s url=%s -> %s", i + 1, r.status_code, current, loc)

            if r.status_code in (301, 302, 303, 307, 308) and loc:
                current = urljoin(current, loc)
                continue
            break

        return current, last, history

    def _extract_code_from_url(self, u):
        try:
            q = parse_qs(urlparse(u).query)
            if "code" in q and q["code"]:
                return q["code"][0]
        except Exception:
            pass
        return ""

    def login_linuxdo_oauth(self, linuxdo_user, linuxdo_pass, client_id):
        if not linuxdo_user or not linuxdo_pass:
            log.error("缺少 LinuxDo 账号密码")
            return False

        if not self._linuxdo_login(linuxdo_user, linuxdo_pass):
            log.error("LinuxDo 登录失败")
            return False

        state = self.get_oauth_state()
        if not state:
            log.error("获取 /api/oauth/state 失败")
            return False

        if not client_id:
            client_id = self.discover_linuxdo_client_id()
        if not client_id:
            log.error("无法获得 LinuxDo client_id")
            return False

        authorize_url = (
            "https://connect.linux.do/oauth2/authorize?"
            + urlencode(
                {
                    "response_type": "code",
                    "client_id": client_id,
                    "state": state,
                }
            )
        )
        log.info("authorize_url=%s", authorize_url)

        final_url, final_r, history = self._walk_redirects(authorize_url)

        # 如停在授权确认页，点击 approve
        if final_r is not None and final_r.status_code == 200 and "connect.linux.do" in final_url:
            approve = re.search(r'href="(/oauth2/approve/[^"]+)"', final_r.text or "")
            if approve:
                approve_url = urljoin(final_url, approve.group(1))
                log.info("发现授权链接，访问: %s", approve_url)
                final_url, final_r, history2 = self._walk_redirects(approve_url)
                history.extend(history2)

        # 尝试从历史 URL 和 final_url 提取 code
        code = self._extract_code_from_url(final_url)
        if not code:
            for u, _, loc in history:
                code = self._extract_code_from_url(u)
                if code:
                    break
                if loc:
                    full = urljoin(u, loc)
                    code = self._extract_code_from_url(full)
                    if code:
                        break

        if not code:
            # 有些场景可能已经重定向并登录成功，直接验证
            user = self.get_self()
            if user:
                log.info("虽然未提取到 code，但 /api/user/self 已可用，判定登录成功")
                return True
            log.error("OAuth 未提取到 code")
            return False

        cb = f"/api/oauth/linuxdo?{urlencode({'code': code, 'state': state})}"
        cr = self._get(cb, allow_redirects=False)
        self._log_resp("GET /api/oauth/linuxdo callback", cr)

        j = self._json(cr)
        if isinstance(j, dict):
            self._update_token_from_json(j)
            self._update_user_id_from_json(j)
            # 即使 success=false，可能也已写 cookie，这里继续验证 self
            if j.get("success") is True:
                log.info("OAuth callback 返回 success=true")

        user = self.get_self()
        if user:
            log.info("OAuth 登录成功: %s", user.get("username") or user.get("display_name") or "unknown")
            return True

        log.error("OAuth 回调后 /api/user/self 仍不可用")
        return False

    # ---------- 签到 ----------
    def get_self(self):
        r = self._get("/api/user/self", headers=self._auth_headers(), allow_redirects=False)
        self._log_resp("GET /api/user/self", r)
        j = self._json(r)
        if isinstance(j, dict) and j.get("success") is True and isinstance(j.get("data"), dict):
            self._update_token_from_json(j)
            self._update_user_id_from_json(j)
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
        self._log_resp(f"GET /api/user/checkin?month={month}", r)
        return self._json(r) or {}

    def do_checkin(self, turnstile_token=""):
        path = "/api/user/checkin"
        if turnstile_token:
            path += "?" + urlencode({"turnstile": turnstile_token})
        r = self._post(path, headers=self._auth_headers(), allow_redirects=False)
        self._log_resp("POST /api/user/checkin", r)
        return self._json(r) or {}

    @staticmethod
    def is_checked_in(status_json):
        """
        兼容多种返回形态
        """
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


def run():
    client = FreestyleClient()
    log.info("BASE_URL=%s", BASE_URL)
    log.info("PROXY=%s", PROXY)
    log.info("LOGIN_MODE=%s", LOGIN_MODE)
    log.info("IMPERSONATE=%s", IMPERSONATE)

    mode = LOGIN_MODE
    if mode not in {"auto", "account", "linuxdo"}:
        return False, f"无效 LOGIN_MODE: {mode}"

    if mode == "auto":
        if FREESTYLE_USERNAME and FREESTYLE_PASSWORD:
            mode = "account"
        elif LINUXDO_USERNAME and LINUXDO_PASSWORD:
            mode = "linuxdo"
        else:
            return False, "未提供可用凭据：请设置 FREESTYLE_USERNAME/PASSWORD 或 FREESTYLE_LINUXDO_USERNAME/PASSWORD"

    # 1) 登录
    if mode == "account":
        ok = client.login_account(FREESTYLE_USERNAME, FREESTYLE_PASSWORD)
        if not ok:
            return False, "账号密码登录失败"
    else:
        ok = client.login_linuxdo_oauth(LINUXDO_USERNAME, LINUXDO_PASSWORD, LINUXDO_CLIENT_ID)
        if not ok:
            return False, "LinuxDo OAuth 登录失败"

    # 2) 自检
    user = client.get_self()
    if not user:
        return False, "登录后 /api/user/self 失败"

    uname = user.get("username") or user.get("display_name") or user.get("email") or "未知"

    # 3) 签到状态
    status = client.get_checkin_status()
    if client.is_checked_in(status):
        return True, f"Freestyle 签到\n✅ 今日已签到\n👤 用户: {uname}"

    # 4) 执行签到
    result = client.do_checkin()
    success = bool(result.get("success") is True)
    msg = str(result.get("message") or "")
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    quota_awarded = data.get("quota_awarded")

    if success:
        extra = f"\n🎁 奖励: {quota_awarded}" if quota_awarded else ""
        return True, f"Freestyle 签到\n✅ 签到成功\n👤 用户: {uname}{extra}"

    if "已签到" in msg or "already" in msg.lower():
        return True, f"Freestyle 签到\n✅ 今日已签到\n👤 用户: {uname}"

    return False, f"Freestyle 签到\n❌ 签到失败: {msg or result}"


def main():
    load_env()

    # TG 配置写死到环境变量，统一复用 utils.send_telegram
    os.environ["TG_BOT_TOKEN"] = HARDCODED_TG_BOT_TOKEN
    os.environ["TG_CHAT_ID"] = HARDCODED_TG_CHAT_ID

    ok, msg = run()
    log.info(msg)
    if TG_ON_SUCCESS or (not ok):
        send_telegram(msg)
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()