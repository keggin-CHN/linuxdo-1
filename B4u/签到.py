"""B4u站 全自动：抽奖 → 获取兑换码 → 兑换"""
import random
import json
import re
import time
import os
import sys
from datetime import datetime
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import get_proxy, create_session, delay, load_env  # noqa: E402
from curl_cffi import requests as cffi_requests  # noqa: E402

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

TW_URL = "https://tw.b4u.qzz.io"
API_URL = "https://b4u.qzz.io"
TW_CLIENT_ID = "mdlRpZG4WoRwjP1yGJDEbrnal7vP0Fb"
API_CLIENT_ID = "Cf3PtT3ecj4kzJrMvOGM48FrHFKYXusb"
TARGETS = ["chrome133a", "chrome136", "chrome142"]

log = __import__("logging").getLogger("B4u")


class B4uClient:
    def __init__(self):
        self.session, self.imp = create_session()
        self.proxy = get_proxy()
        self.kw = dict(proxy=self.proxy, timeout=30)
        self.username = None
        self.api_user_id = None
        self.build_id = None
        self.action_get_remaining = None
        self.action_draw = None

    def login_linuxdo(self):
        r = self.session.get("https://linux.do/", allow_redirects=True, **self.kw)
        time.sleep(random.uniform(1, 2))
        r = self.session.get("https://linux.do/session/csrf.json", **self.kw)
        try:
            csrf = r.json().get("csrf")
        except Exception:
            raise RuntimeError(f"CSRF 响应非 JSON (HTTP {r.status_code}): {r.text[:200]}")
        if not csrf:
            raise RuntimeError("CSRF token 为空")
        time.sleep(random.uniform(1, 2))
        r = self.session.post(
            "https://linux.do/session",
            data={
                "login": os.environ["CDK_USERNAME"],
                "password": os.environ["CDK_PASSWORD"],
                "second_factor_method": "1",
            },
            headers={"X-CSRF-Token": csrf, "X-Requested-With": "XMLHttpRequest"},
            **self.kw,
        )
        try:
            data = r.json()
        except Exception:
            raise RuntimeError(f"登录响应非 JSON (HTTP {r.status_code}): {r.text[:200]}")
        user = data.get("user", {}).get("username")
        if not user:
            raise RuntimeError(f"LinuxDo 登录失败: {data.get('error', 'unknown')}")
        return user

    def _fetch_dynamic_ids(self):
        r = self.session.get(f"{TW_URL}/dashboard", **self.kw)
        if r.status_code != 200:
            raise RuntimeError(f"获取 dashboard 失败: {r.status_code}")

        m = re.search(r'"buildId"\s*:\s*"([a-zA-Z0-9_-]+)"', r.text)
        if not m:
            m = re.search(r'\\"buildId\\"\s*:\s*\\"([a-zA-Z0-9_-]+)\\"', r.text)
        if not m:
            m = re.search(r'/_next/static/([a-zA-Z0-9_-]{10,30})/_', r.text)
        if not m:
            raise RuntimeError("无法提取 buildId")
        self.build_id = m.group(1)

        time.sleep(random.uniform(0.5, 1))
        rsc = self.session.get(
            f"{TW_URL}/_next/data/{self.build_id}/luckydraw.json",
            headers={"RSC": "1"}, **self.kw,
        )
        if rsc.status_code != 200:
            raise RuntimeError(f"获取 luckydraw RSC 失败: {rsc.status_code}")

        luckydraw_chunks = re.findall(
            r'static/chunks/[^"\'\\,\]\s]+luckydraw[^"\'\\,\]\s]+\.js', rsc.text,
        )
        if not luckydraw_chunks:
            raise RuntimeError("RSC 数据中未找到 luckydraw chunk")

        chunk_path = luckydraw_chunks[0]
        time.sleep(random.uniform(0.3, 0.8))
        jr = self.session.get(f"{TW_URL}/_next/{chunk_path}", **self.kw)
        if jr.status_code != 200:
            raise RuntimeError(f"下载 chunk 失败: {jr.status_code}")

        chunk_text = jr.text
        all_action_ids = re.findall(r'\(0,[a-zA-Z]\.\$\)\("([a-f0-9]{40})"\)', chunk_text)
        if not all_action_ids:
            all_action_ids = re.findall(r'"([a-f0-9]{40})"', chunk_text)
        if len(all_action_ids) < 2:
            raise RuntimeError(f"只找到 {len(all_action_ids)} 个 action ID")

        self.action_get_remaining = None
        self.action_draw = None

        kx_match = re.search(r'KX:function\(\)\{return\s+(\w+)\}', chunk_text)
        if kx_match:
            var_name = kx_match.group(1)
            pat = re.escape(var_name) + r'=\(0,[a-zA-Z]\.\$\)\("([a-f0-9]{40})"\)'
            m = re.search(pat, chunk_text)
            if m:
                self.action_get_remaining = m.group(1)

        et_match = re.search(r'await\s+(\w+)\(\{excludeThankYou:', chunk_text)
        if et_match:
            var_name = et_match.group(1)
            pat = re.escape(var_name) + r'=\(0,[a-zA-Z]\.\$\)\("([a-f0-9]{40})"\)'
            m = re.search(pat, chunk_text)
            if m:
                self.action_draw = m.group(1)

        if not self.action_get_remaining or not self.action_draw:
            if len(all_action_ids) >= 9:
                if not self.action_get_remaining:
                    self.action_get_remaining = all_action_ids[5]
                if not self.action_draw:
                    self.action_draw = all_action_ids[8]
            elif len(all_action_ids) >= 2:
                if not self.action_get_remaining:
                    self.action_get_remaining = all_action_ids[0]
                if not self.action_draw:
                    self.action_draw = all_action_ids[-1]

        if not self.action_get_remaining or not self.action_draw:
            raise RuntimeError(f"无法识别 action IDs (共{len(all_action_ids)}个)")

    def _oauth_authorize(self, client_id, redirect_uri=None, state=None):
        params = f"response_type=code&client_id={client_id}"
        if state:
            params += f"&state={state}"
        if redirect_uri:
            params += f"&redirect_uri={redirect_uri}"

        authorize_url = f"https://connect.linux.do/oauth2/authorize?{params}"
        r = self.session.get(authorize_url, allow_redirects=False, **self.kw)

        max_hops = 20
        hop = 0
        while r.status_code in (301, 302, 303, 307, 308) and hop < max_hops:
            hop += 1
            loc = r.headers.get("location", "")
            if loc.startswith("/"):
                p = urlparse(str(getattr(r, "url", authorize_url)))
                loc = f"{p.scheme}://{p.netloc}{loc}"
            if "code=" in loc:
                return parse_qs(urlparse(loc).query).get("code", [""])[0]
            time.sleep(0.5)
            r = self.session.get(loc, allow_redirects=False, **self.kw)

        if r.status_code == 200 and "connect.linux.do" in str(getattr(r, "url", "")):
            approve = re.search(r'href="(/oauth2/approve/[^"]+)"', r.text)
            if approve:
                time.sleep(1)
                r = self.session.get(f"https://connect.linux.do{approve.group(1)}",
                                     allow_redirects=False, **self.kw)
                while r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers.get("location", "")
                    if loc.startswith("/"):
                        p = urlparse(str(getattr(r, "url", "")))
                        loc = f"{p.scheme}://{p.netloc}{loc}"
                    if "code=" in loc:
                        return parse_qs(urlparse(loc).query).get("code", [""])[0]
                    time.sleep(0.5)
                    r = self.session.get(loc, allow_redirects=False, **self.kw)
        return None

    def login_tw(self):
        r = self.session.get(f"{TW_URL}/api/auth/csrf", **self.kw)
        na_csrf = r.json().get("csrfToken", "")
        self.session.get("https://connect.linux.do/", allow_redirects=True, **self.kw)
        time.sleep(random.uniform(1, 2))
        r = self.session.post(
            f"{TW_URL}/api/auth/signin/linuxdo",
            data={"csrfToken": na_csrf, "callbackUrl": TW_URL, "json": "true"},
            allow_redirects=False, **self.kw,
        )
        loc = r.headers.get("location", "")
        if not loc:
            raise RuntimeError("NextAuth signin 未返回重定向")
        r = self.session.get(loc, allow_redirects=False, **self.kw)
        while r.status_code in (301, 302, 303, 307, 308):
            l2 = r.headers.get("location", "")
            if l2.startswith("/"):
                p = urlparse(str(getattr(r, "url", loc)))
                l2 = f"{p.scheme}://{p.netloc}{l2}"
            time.sleep(0.5)
            r = self.session.get(l2, allow_redirects=False, **self.kw)
        if r.status_code == 200 and "connect.linux.do" in str(getattr(r, "url", "")):
            approve = re.search(r'href="(/oauth2/approve/[^"]+)"', r.text)
            if approve:
                time.sleep(1)
                r = self.session.get(f"https://connect.linux.do{approve.group(1)}",
                                     allow_redirects=False, **self.kw)
                while r.status_code in (301, 302, 303, 307, 308):
                    l2 = r.headers.get("location", "")
                    if l2.startswith("/"):
                        p = urlparse(str(getattr(r, "url", "")))
                        l2 = f"{p.scheme}://{p.netloc}{l2}"
                    time.sleep(0.5)
                    r = self.session.get(l2, allow_redirects=False, **self.kw)
        time.sleep(1)
        r = self.session.get(f"{TW_URL}/api/auth/session", **self.kw)
        sess = r.json()
        user = sess.get("user", {})
        if not user.get("name"):
            raise RuntimeError("tw.b4u 登录失败")
        self.username = user["name"]
        return user

    def login_api(self):
        r = self.session.get(f"{API_URL}/api/oauth/state", **self.kw)
        if r.status_code != 200:
            raise RuntimeError(f"获取 state HTTP {r.status_code}")
        data = r.json()
        if not data.get("success") or not data.get("data"):
            raise RuntimeError(f"获取 state 失败: {data}")
        state = data["data"]
        time.sleep(random.uniform(1, 2))
        code = self._oauth_authorize(API_CLIENT_ID, state=state)
        if not code:
            raise RuntimeError("b4u.qzz.io OAuth 未获取到 code")
        time.sleep(random.uniform(1, 2))
        r = self.session.get(f"{API_URL}/api/oauth/linuxdo",
                             params={"code": code, "state": state}, **self.kw)
        if r.status_code != 200:
            raise RuntimeError(f"b4u.qzz.io 登录 HTTP {r.status_code}")
        data = r.json()
        if not data.get("success"):
            raise RuntimeError(f"b4u.qzz.io 登录失败: {data.get('message')}")
        self.api_user_id = data["data"].get("id")
        return data["data"]

    def login(self):
        ld_user = self.login_linuxdo()
        log.info(f"LinuxDo: {ld_user}")
        time.sleep(random.uniform(2, 3))
        user = self.login_tw()
        log.info(f"tw.b4u: {user['name']}")
        time.sleep(random.uniform(1, 2))
        api_user = self.login_api()
        log.info(f"b4u API: id={self.api_user_id}")
        return user

    def call_action(self, action_id, args=None, path=None):
        if args is None:
            args = []
        headers = {
            "Next-Action": action_id,
            "Accept": "text/x-component",
            "Next-Router-State-Tree": '%5B%22%22%2C%7B%22children%22%3A%5B%22(dashboard)%22%2C%7B%22children%22%3A%5B%22luckydraw%22%2C%7B%22children%22%3A%5B%22__PAGE__%22%2C%7B%7D%5D%7D%5D%7D%5D%7D%2Cnull%2Cnull%2Ctrue%5D',
        }
        if path is None:
            path = f"/_next/data/{self.build_id}/luckydraw.json"
        r = self.session.post(f"{TW_URL}{path}", headers=headers, json=args, **self.kw)
        if r.status_code != 200:
            raise RuntimeError(f"Server Action 失败: {r.status_code}")
        ct = r.headers.get("content-type", "")
        if "text/html" in ct and "<html" in r.text[:200].lower():
            raise RuntimeError(f"Server Action 返回 HTML")
        return r

    def parse_action_response(self, text):
        for line in text.strip().split("\n"):
            if line.startswith("1:"):
                data_str = line[2:]
                try:
                    return json.loads(data_str)
                except json.JSONDecodeError:
                    try:
                        return int(data_str)
                    except ValueError:
                        return data_str
        return text

    def get_remaining(self):
        r = self.call_action(self.action_get_remaining,
                             path=f"/_next/data/{self.build_id}/luckydraw.json")
        return self.parse_action_response(r.text)

    def draw(self, exclude_thank_you=False):
        r = self.call_action(self.action_draw,
                             [{"excludeThankYou": exclude_thank_you}],
                             path="/dashboard")
        return self.parse_action_response(r.text)

    def redeem_code(self, code):
        r = self.session.post(
            f"{API_URL}/api/user/topup",
            json={"key": code},
            headers={"New-Api-User": str(self.api_user_id)},
            **self.kw,
        )
        if not r.text.strip():
            return {"success": False, "message": f"空响应 HTTP {r.status_code}"}
        try:
            return r.json()
        except Exception:
            return {"success": False, "message": f"非JSON({r.status_code})"}

    def get_quota(self):
        r = self.session.get(
            f"{API_URL}/api/user/self",
            headers={"New-Api-User": str(self.api_user_id)},
            **self.kw,
        )
        if r.status_code == 200:
            data = r.json().get("data", {})
            return data.get("quota", 0), data.get("used_quota", 0)
        return 0, 0


def run():
    """执行 B4u 全流程，返回 (成功?, 消息)"""
    client = B4uClient()

    client.login()
    time.sleep(random.uniform(1, 2))
    client._fetch_dynamic_ids()

    time.sleep(random.uniform(1, 2))
    remaining = client.get_remaining()

    draw_results = []
    new_codes = []

    if isinstance(remaining, int) and remaining > 0:
        for i in range(remaining):
            time.sleep(random.uniform(2, 4))
            try:
                result = client.draw()
                if isinstance(result, dict) and result.get("success"):
                    prize = result.get("prize", {})
                    name = prize.get("name", "未知")
                    value = prize.get("value", 0)
                    code = result.get("redemptionCode", "")
                    if code:
                        new_codes.append(code)
                    draw_results.append(f"✅ {name}({value}元)")
                elif isinstance(result, dict):
                    err = result.get("message", "未知错误")
                    draw_results.append(f"❌ {err}")
                    if "次数" in err:
                        break
                else:
                    draw_results.append(f"⚠️ {str(result)[:50]}")
            except Exception as e:
                draw_results.append(f"❌ {e}")
    else:
        draw_results.append("今日次数已用完")

    redeem_results = []
    redeem_success = 0
    redeem_total_value = 0

    if new_codes:
        for code in new_codes:
            time.sleep(random.uniform(2, 4))
            try:
                result = client.redeem_code(code)
                if result.get("success"):
                    value = result.get("data", 0)
                    usd = value / 500_000 if value else 0
                    redeem_success += 1
                    redeem_total_value += value
                    redeem_results.append(f"✅ {code[:8]}...(+${usd:.2f})")
                else:
                    msg = result.get("message", "未知")
                    redeem_results.append(f"❌ {code[:8]}...({msg})")
            except Exception as e:
                redeem_results.append(f"❌ {code[:8]}...({e})")

    time.sleep(1)
    quota, used = client.get_quota()
    remaining_usd = (quota - used) / 500_000

    draw_summary = "\n".join(draw_results) if draw_results else "无"
    redeem_value_usd = redeem_total_value / 500_000 if redeem_total_value else 0

    total_usd = quota / 500_000

    lines = [
        f"🎰 B4u | {client.username}",
        f"抽奖({remaining if isinstance(remaining, int) else 0}次):",
        draw_summary,
        "",
        f"兑换: {redeem_success}/{len(new_codes)} 成功" + (f" (+${redeem_value_usd:.2f})" if redeem_value_usd else ""),
        "",
        f"💰 额度: ${total_usd:.0f}",
    ]
    msg = "\n".join(lines)
    return True, msg


def main():
    load_env()
    from utils import send_telegram
    try:
        ok, msg = run()
        log.info(msg)
        send_telegram(msg)
    except Exception as e:
        msg = f"🎰 B4u\n❌ 失败: {e}"
        log.error(msg)
        send_telegram(msg)
        sys.exit(1)


if __name__ == "__main__":
    main()