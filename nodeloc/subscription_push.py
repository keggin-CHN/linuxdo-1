#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VIP 订阅自动获取并推送 TG
流程:
1) NodeLoc 协议登录
2) 触发 VIP 的 /auth/nodeloc OAuth
3) 获取首页中的个人订阅链接（id=sub-link）
4) 推送到 Telegram，并保存本地状态（用于识别是否变化）
"""

import os
import re
import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utils import load_env, create_session, get_proxy, send_telegram  # noqa: E402

VIP_BASE = "https://vip.vip.sd"
NODELOC_BASE = "https://www.nodeloc.com"

# 可被 .env 覆盖
DEFAULT_NODELOC_USERNAME = "zhou060423rls@gmail.com"
DEFAULT_NODELOC_PASSWORD = "Zhou060423rls"

STATE_FILE = Path(__file__).resolve().parent / "vip_subscription_state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vip-sub")


def _bj_now_str() -> str:
    bj = timezone(timedelta(hours=8))
    return datetime.now(bj).strftime("%Y-%m-%d %H:%M:%S")


def _read_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_state(data: dict):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"写入状态文件失败: {e}")


def _extract_sub_link(html: str) -> str:
    # 优先匹配 input#sub-link
    m = re.search(
        r'<input[^>]*id=["\']sub-link["\'][^>]*value=["\']([^"\']+)["\']',
        html,
        flags=re.I,
    )
    if not m:
        # 兼容 value 在前、id 在后
        m = re.search(
            r'<input[^>]*value=["\']([^"\']+)["\'][^>]*id=["\']sub-link["\']',
            html,
            flags=re.I,
        )
    if m:
        return m.group(1).strip()

    # 兜底：直接抓取 sub UUID 链接
    m2 = re.search(r"https://vip\.vip\.sd/sub/[0-9a-fA-F-]{36}", html, flags=re.I)
    return m2.group(0).strip() if m2 else ""


class VipSubClient:
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.proxy = get_proxy()
        self.session, self.impersonate = create_session(proxy=self.proxy)
        log.info(f"浏览器指纹: {self.impersonate} | proxy={self.proxy}")

    def _get(self, url, **kwargs):
        kwargs.setdefault("timeout", 30)
        kwargs.setdefault("proxy", self.proxy)
        return self.session.get(url, **kwargs)

    def _post(self, url, **kwargs):
        kwargs.setdefault("timeout", 30)
        kwargs.setdefault("proxy", self.proxy)
        return self.session.post(url, **kwargs)

    def _follow_redirects(self, start_url: str, max_hops: int = 20):
        url = start_url
        last = None
        for i in range(max_hops):
            r = self._get(url, allow_redirects=False)
            last = r
            loc = r.headers.get("location", "")
            log.info(f"[hop {i + 1}] {r.status_code} {url} -> {loc}")
            if r.status_code in (301, 302, 303, 307, 308) and loc:
                url = loc if loc.startswith("http") else urljoin(url, loc)
                continue
            break
        return last

    def ensure_nodeloc_login(self):
        # 若已登录，直接跳过
        current = self._get(f"{NODELOC_BASE}/session/current.json", allow_redirects=False)
        if current.status_code == 200 and "application/json" in (current.headers.get("content-type", "")):
            try:
                data = current.json()
                cu = data.get("current_user") if isinstance(data, dict) else None
                if cu and cu.get("username"):
                    log.info(f"NodeLoc 已登录: {cu.get('username')}")
                    return
            except Exception:
                pass

        # 获取 CSRF
        csrf_resp = self._get(f"{NODELOC_BASE}/session/csrf.json", allow_redirects=False)
        if csrf_resp.status_code != 200:
            raise RuntimeError(f"获取 NodeLoc CSRF 失败: {csrf_resp.status_code}")
        csrf = csrf_resp.json().get("csrf", "")
        if not csrf:
            raise RuntimeError("NodeLoc CSRF 为空")

        # 登录
        login_resp = self._post(
            f"{NODELOC_BASE}/session",
            data={
                "login": self.username,
                "password": self.password,
                "second_factor_method": "1",
            },
            headers={
                "X-CSRF-Token": csrf,
                "X-Requested-With": "XMLHttpRequest",
                "Origin": NODELOC_BASE,
                "Referer": f"{NODELOC_BASE}/login",
            },
            allow_redirects=True,
        )
        log.info(f"NodeLoc 登录响应: {login_resp.status_code}")

        ok = False
        try:
            j = login_resp.json()
            if isinstance(j, dict) and j.get("error"):
                raise RuntimeError(f"NodeLoc 登录失败: {j.get('error')}")
            user = j.get("user") if isinstance(j, dict) else None
            if user and user.get("username"):
                ok = True
                log.info(f"NodeLoc 登录成功: {user.get('username')}")
        except Exception:
            pass

        # 兜底检查
        current = self._get(f"{NODELOC_BASE}/session/current.json", allow_redirects=False)
        if current.status_code == 200 and "application/json" in (current.headers.get("content-type", "")):
            try:
                data = current.json()
                cu = data.get("current_user") if isinstance(data, dict) else None
                if cu and cu.get("username"):
                    ok = True
            except Exception:
                pass

        if not ok:
            raise RuntimeError("NodeLoc 登录后未检测到登录态")

    def fetch_subscription_link(self) -> str:
        # 触发 OAuth（VIP -> NodeLoc -> VIP callback）
        last = self._follow_redirects(f"{VIP_BASE}/auth/nodeloc", max_hops=20)
        if not last:
            raise RuntimeError("OAuth 流程未返回结果")

        # 最终访问 VIP 首页获取订阅
        home = self._get(f"{VIP_BASE}/", allow_redirects=True)
        log.info(f"VIP 首页状态: {home.status_code}, final_url={getattr(home, 'url', VIP_BASE + '/')}")
        if home.status_code != 200:
            raise RuntimeError(f"VIP 首页访问失败: {home.status_code}")

        sub_link = _extract_sub_link(home.text or "")
        if not sub_link:
            raise RuntimeError("未在 VIP 首页提取到订阅链接")
        return sub_link.strip()


def run(send_tg=send_telegram):
    username = os.environ.get("NODELOC_USERNAME", DEFAULT_NODELOC_USERNAME).strip()
    password = os.environ.get("NODELOC_PASSWORD", DEFAULT_NODELOC_PASSWORD).strip()
    push_only_change = os.environ.get("VIP_PUSH_ONLY_ON_CHANGE", "0").strip() in {"1", "true", "True"}

    if not username or not password:
        return False, "NodeLoc 订阅推送\n❌ NODELOC_USERNAME 或 NODELOC_PASSWORD 为空"

    try:
        client = VipSubClient(username=username, password=password)
        client.ensure_nodeloc_login()
        sub_link = client.fetch_subscription_link()

        state = _read_state()
        old_link = (state.get("last_sub_link") or "").strip()
        changed = (sub_link != old_link)

        new_state = {
            "last_sub_link": sub_link,
            "updated_at": _bj_now_str(),
            "username": username,
        }
        _write_state(new_state)

        status = "🆕 已变更" if changed else "♻️ 未变化"
        msg = "\n".join(
            [
                "NodeLoc 订阅同步",
                f"✅ {status}",
                f"👤 账号: {username}",
                f"🕒 时间: {_bj_now_str()}",
                f"🔗 订阅: {sub_link}",
            ]
        )

        if (not push_only_change) or changed:
            send_tg(msg)
        log.info(msg)
        return True, msg

    except Exception as e:
        err = f"NodeLoc 订阅同步\n❌ 失败: {e}"
        log.error(err)
        try:
            send_tg(err)
        except Exception:
            pass
        return False, err


def main():
    load_env()
    ok, msg = run()
    log.info(msg)
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()