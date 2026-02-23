"""从 /nav 站点列表并发扫描可签到站点（LinuxDo OAuth 登录 + 按钮/能力检测）"""
import json
import os
import re
import sys
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import create_session, delay, get_proxy, load_env  # noqa: E402


# /nav 页面背后使用的注册中心接口（来自 _workers_next/src/lib/registry.ts）
REGISTRY_URL = os.environ.get("NAV_REGISTRY_URL", "https://ldcnavi.chatgptuk.workers.dev/shops?limit=300")
NAV_PAGE_URL = os.environ.get("NAV_PAGE_URL", "https://shop.chatgpt.org.uk/nav")

# 按你当前项目测试习惯，默认走本地 10808 代理
HARDCODED_PROXY = "http://127.0.0.1:10808"

# 账号优先环境变量，缺失时回退硬编码（与 shop/签到.py 一致）
HARDCODED_CDK_USERNAME = "zhou239289001@gmail.com"
HARDCODED_CDK_PASSWORD = "zhou060423rls"

TIMEOUT = 20
MAX_WORKERS = int(os.environ.get("NAV_SCAN_WORKERS", "12"))
LIMIT = int(os.environ.get("NAV_SCAN_LIMIT", "0"))  # 0 = 不限
ROOT_TREE = "%5B%22%22%2C%7B%22children%22%3A%5B%22__PAGE__%22%2C%7B%7D%5D%7D%2Cnull%2Cnull%2Ctrue%5D"

log = logging.getLogger("nav_scan")


def normalize_origin(url: str) -> str:
    try:
        p = urlparse(url.strip())
        if p.scheme != "https":
            return ""
        if not p.netloc:
            return ""
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


def parse_registry_items(text: str):
    """尽可能兼容不同格式的 registry 返回"""
    try:
        data = json.loads(text)
    except Exception:
        return []

    # 标准: {"items":[...]}
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        out = []
        for it in data["items"]:
            if not isinstance(it, dict):
                continue
            url = normalize_origin(str(it.get("url", "")))
            if not url:
                continue
            out.append(
                {
                    "name": str(it.get("name", "")).strip() or urlparse(url).netloc,
                    "url": url,
                    "description": str(it.get("description", "")).strip(),
                    "logo": str(it.get("logo", "")).strip(),
                }
            )
        return out

    # 兼容: 直接数组
    if isinstance(data, list):
        out = []
        for it in data:
            if not isinstance(it, dict):
                continue
            url = normalize_origin(str(it.get("url", "")))
            if not url:
                continue
            out.append(
                {
                    "name": str(it.get("name", "")).strip() or urlparse(url).netloc,
                    "url": url,
                    "description": str(it.get("description", "")).strip(),
                    "logo": str(it.get("logo", "")).strip(),
                }
            )
        return out

    return []


def fetch_nav_sites(proxy: str):
    """先读 registry，失败再从 /nav 页面抓链接"""
    session, imp = create_session()
    log.info("fetch nav list impersonate=%s", imp)

    # 1) registry
    try:
        r = session.get(REGISTRY_URL, proxy=proxy, timeout=TIMEOUT)
        if r.status_code == 200:
            items = parse_registry_items(r.text)
            if items:
                log.info("registry items=%s", len(items))
                return items
            log.warning("registry parse empty")
        else:
            log.warning("registry status=%s", r.status_code)
    except Exception as e:
        log.warning("registry error: %s", e)

    # 2) fallback: /nav 页抓外链
    try:
        r = session.get(NAV_PAGE_URL, proxy=proxy, timeout=TIMEOUT)
        if r.status_code != 200:
            log.warning("/nav fallback status=%s", r.status_code)
            return []

        hrefs = re.findall(r'href="(https://[^"]+)"', r.text, re.I)
        seen = set()
        out = []
        for h in hrefs:
            u = normalize_origin(h)
            if not u:
                continue
            if u in seen:
                continue
            seen.add(u)
            out.append({"name": urlparse(u).netloc, "url": u, "description": "", "logo": ""})
        log.info("/nav fallback items=%s", len(out))
        return out
    except Exception as e:
        log.warning("/nav fallback error: %s", e)
        return []


class ProbeClient:
    def __init__(self, base_url: str, proxy: str):
        self.base_url = base_url.rstrip("/")
        self.proxy = proxy
        self.session, self.imp = create_session()

    def _get(self, url, **kw):
        kw.setdefault("proxy", self.proxy)
        kw.setdefault("timeout", TIMEOUT)
        return self.session.get(url, **kw)

    def _post(self, url, **kw):
        kw.setdefault("proxy", self.proxy)
        kw.setdefault("timeout", TIMEOUT)
        return self.session.post(url, **kw)

    def set_cookie_string(self, cookie_str: str, domain: str):
        if not cookie_str:
            return
        for part in cookie_str.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            name, value = part.split("=", 1)
            name = name.strip()
            value = value.strip()
            if name:
                self.session.cookies.set(name, value, domain=domain)

    def export_cookie_string(self, domain_keyword: str):
        pairs = []
        if hasattr(self.session.cookies, "items"):
            for k, v in self.session.cookies.items():
                # 对 requests/curl_cffi 的简化 cookiejar 兼容：无法精准取 domain，先全收集
                pairs.append(f"{k}={v}")
        return "; ".join(pairs)

    def verify_login(self):
        try:
            r = self._get(f"{self.base_url}/api/auth/session")
            if r.status_code != 200:
                return None
            data = r.json()
            if isinstance(data, dict) and isinstance(data.get("user"), dict):
                return data["user"]
        except Exception:
            return None
        return None

    def login_linuxdo(self, username: str, password: str):
        # 只在目标站登录失败时兜底调用
        try:
            r = self._get("https://linux.do/session/csrf.json")
            if r.status_code != 200:
                return False
            csrf = (r.json() or {}).get("csrf")
            if not csrf:
                return False
            delay(0.5, 1.2)
            r = self._post(
                "https://linux.do/session",
                data={"login": username, "password": password, "second_factor_method": "1"},
                headers={"X-CSRF-Token": csrf, "X-Requested-With": "XMLHttpRequest"},
            )
            if r.status_code != 200:
                return False
            data = r.json()
            return bool(isinstance(data, dict) and data.get("user", {}).get("username"))
        except Exception:
            return False

    def _abs_url(self, current_url, loc):
        if not loc:
            return ""
        if loc.startswith("/"):
            p = urlparse(current_url)
            return f"{p.scheme}://{p.netloc}{loc}"
        return loc

    def _extract_meta_refresh(self, html, current_url):
        m = re.search(r'http-equiv=["\']refresh["\'][^>]*url=([^"\'>\s]+)', html or "", re.I)
        if not m:
            return ""
        return self._abs_url(current_url, m.group(1).strip())

    def _extract_js_redirect(self, html, current_url):
        for pat in [
            r'location\.href\s*=\s*["\']([^"\']+)["\']',
            r'window\.location\s*=\s*["\']([^"\']+)["\']',
            r'window\.location\.replace\(["\']([^"\']+)["\']\)',
        ]:
            m = re.search(pat, html or "", re.I)
            if m:
                return self._abs_url(current_url, m.group(1).strip())
        return ""

    def _follow_oauth_flow(self, url, max_hops=25):
        r = self._get(url, allow_redirects=False)
        current = url
        hops = 0

        while hops < max_hops:
            final_url = str(getattr(r, "url", "") or current)

            if r.status_code in (301, 302, 303, 307, 308):
                loc = self._abs_url(final_url, r.headers.get("location", ""))
                if not loc:
                    return r
                if "/api/auth/callback/linuxdo" in loc:
                    return self._get(loc, allow_redirects=True)
                r = self._get(loc, allow_redirects=False)
                current = loc
                hops += 1
                continue

            if r.status_code == 200 and "connect.linux.do" in final_url:
                approve = re.search(r'href="(/oauth2/approve/[^"]+)"', r.text or "")
                if approve:
                    u = self._abs_url(final_url, approve.group(1))
                    r = self._get(u, allow_redirects=False)
                    current = u
                    hops += 1
                    continue

                u = self._extract_meta_refresh(r.text, final_url)
                if u:
                    r = self._get(u, allow_redirects=False)
                    current = u
                    hops += 1
                    continue

                u = self._extract_js_redirect(r.text, final_url)
                if u:
                    r = self._get(u, allow_redirects=False)
                    current = u
                    hops += 1
                    continue

            return r

        return r

    def login_target(self):
        # 目标站 OAuth 登录
        try:
            r = self._get(f"{self.base_url}/api/auth/csrf")
            if r.status_code != 200:
                return False
            csrf_token = (r.json() or {}).get("csrfToken")
            if not csrf_token:
                return False

            delay(0.2, 0.6)
            r = self._post(
                f"{self.base_url}/api/auth/signin/linuxdo",
                data={"csrfToken": csrf_token, "callbackUrl": self.base_url, "json": "true"},
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Auth-Return-Redirect": "1",
                },
                allow_redirects=False,
            )

            auth_url = ""
            try:
                auth_url = (r.json() or {}).get("url", "")
            except Exception:
                auth_url = ""
            if not auth_url:
                auth_url = r.headers.get("location", "")
            if not auth_url:
                return False

            self._follow_oauth_flow(auth_url)
            if self.verify_login():
                return True

            # prompt=none 再试一次
            p = urlparse(auth_url)
            if p.netloc == "connect.linux.do":
                q = dict(parse_qsl(p.query, keep_blank_values=True))
                if "prompt" not in q:
                    q["prompt"] = "none"
                    silent_url = urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q), p.fragment))
                    self._follow_oauth_flow(silent_url)

            return bool(self.verify_login())
        except Exception:
            return False

    def has_checkin_button(self):
        # 用户指定：/profile 页面里的签到按钮
        try:
            r = self._get(f"{self.base_url}/profile", allow_redirects=True)
            if r.status_code != 200:
                return False
            html = r.text or ""
            if re.search(r">\s*签到\s*<", html):
                return True
            # 英文站点兜底
            if re.search(r">\s*(Check[- ]?in|Check in)\s*<", html, re.I):
                return True
            # Gift icon + points card 也算命中迹象
            if "lucide-gift" in html and ("积分" in html or "Points" in html):
                return True
        except Exception:
            return False
        return False

    def has_checkin_capability(self):
        # 按架构特征探测：checkIn/getCheckinStatus server actions
        try:
            r = self._get(f"{self.base_url}/", allow_redirects=True)
            if r.status_code != 200:
                return False
            html = r.text or ""
            chunk_paths = re.findall(r'<script[^>]+src="([^"]*/_next/static/chunks/[^"]+\.js)"', html, re.I)
            checked = 0
            for cp in chunk_paths[:25]:
                u = cp if cp.startswith("http") else f"{self.base_url}{cp}"
                jr = self._get(u)
                if jr.status_code != 200:
                    continue
                checked += 1
                t = jr.text or ""
                if "getCheckinStatus" in t and "checkIn" in t:
                    return True
            return checked > 0 and False
        except Exception:
            return False


def bootstrap_linuxdo_cookie(proxy: str, username: str, password: str):
    c = ProbeClient("https://shop.chatgpt.org.uk", proxy=proxy)
    ok = c.login_linuxdo(username, password)
    if not ok:
        return ""
    return c.export_cookie_string("linux.do")


def probe_one(site: dict, proxy: str, username: str, password: str, linuxdo_cookie: str):
    base = site["url"]
    name = site.get("name") or urlparse(base).netloc
    out = {
        "name": name,
        "url": base,
        "login_ok": False,
        "has_checkin_button": False,
        "has_checkin_capability": False,
        "can_checkin": False,
        "error": "",
    }

    try:
        c = ProbeClient(base_url=base, proxy=proxy)

        # 注入 bootstrap 的 LinuxDo cookie，加速 OAuth
        if linuxdo_cookie:
            c.set_cookie_string(linuxdo_cookie, "linux.do")
            c.set_cookie_string(linuxdo_cookie, ".linux.do")
            c.set_cookie_string(linuxdo_cookie, "connect.linux.do")

        if not c.login_target():
            # 兜底：每站显式 LinuxDo 登录一次再进 OAuth
            if c.login_linuxdo(username, password):
                c.login_target()

        out["login_ok"] = bool(c.verify_login())
        if not out["login_ok"]:
            out["error"] = "login_failed"
            return out

        out["has_checkin_button"] = c.has_checkin_button()
        out["has_checkin_capability"] = c.has_checkin_capability()
        out["can_checkin"] = out["has_checkin_button"] or out["has_checkin_capability"]
        return out
    except Exception as e:
        out["error"] = str(e)
        return out


def main():
    load_env()

    username = os.environ.get("CDK_USERNAME", "").strip() or HARDCODED_CDK_USERNAME
    password = os.environ.get("CDK_PASSWORD", "").strip() or HARDCODED_CDK_PASSWORD
    proxy = os.environ.get("CDK_PROXY", "").strip() or HARDCODED_PROXY or get_proxy()

    if not username or not password:
        raise RuntimeError("缺少 CDK_USERNAME/CDK_PASSWORD")

    log.info("proxy=%s workers=%s limit=%s", proxy, MAX_WORKERS, LIMIT or "ALL")

    sites = fetch_nav_sites(proxy=proxy)
    # 去重
    seen = set()
    dedup = []
    for s in sites:
        u = normalize_origin(s.get("url", ""))
        if not u or u in seen:
            continue
        seen.add(u)
        s["url"] = u
        dedup.append(s)

    if LIMIT > 0:
        dedup = dedup[:LIMIT]

    log.info("scan targets=%s", len(dedup))
    if not dedup:
        print("没有拿到 /nav 站点列表")
        return

    # 先拿一次 LinuxDo cookie，提升并发登录成功率
    linuxdo_cookie = bootstrap_linuxdo_cookie(proxy, username, password)
    if linuxdo_cookie:
        log.info("bootstrap linuxdo cookie ok")
    else:
        log.warning("bootstrap linuxdo cookie failed, each site will self-login")

    start = time.time()
    results = []
    can_sites = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        fut_map = {
            ex.submit(probe_one, s, proxy, username, password, linuxdo_cookie): s
            for s in dedup
        }
        done = 0
        total = len(fut_map)
        for fut in as_completed(fut_map):
            done += 1
            r = fut.result()
            results.append(r)

            status = "✅" if r["can_checkin"] else "❌"
            log.info(
                "[%s/%s] %s %s | login=%s button=%s cap=%s",
                done, total, status, r["url"], r["login_ok"], r["has_checkin_button"], r["has_checkin_capability"],
            )

            if r["can_checkin"]:
                can_sites.append(r["url"])

    # 输出文件
    results.sort(key=lambda x: x["url"])
    can_sites = sorted(set(can_sites))

    os.makedirs("linuxdo", exist_ok=True)
    json_path = "linuxdo/nav_checkin_scan_result.json"
    txt_path = "linuxdo/nav_checkin_sites.txt"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "generated_at": int(time.time()),
                "count_total": len(results),
                "count_can_checkin": len(can_sites),
                "proxy": proxy,
                "workers": MAX_WORKERS,
                "items": results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    with open(txt_path, "w", encoding="utf-8") as f:
        for u in can_sites:
            f.write(u + "\n")

    elapsed = time.time() - start
    print("=" * 60)
    print(f"扫描完成: total={len(results)} can_checkin={len(can_sites)} elapsed={elapsed:.1f}s")
    print(f"结果JSON: {json_path}")
    print(f"可签到列表: {txt_path}")
    if can_sites:
        print("- 可签到站点 -")
        for u in can_sites:
            print(u)


if __name__ == "__main__":
    main()