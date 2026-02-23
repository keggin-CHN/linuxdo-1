"""shop.chatgpt.org.uk 自动签到（Linux DO OAuth + Next.js Server Action）"""
import json
import os
import re
import sys
import time
import html as html_lib
import logging
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import create_session, delay, load_env, CompatSession  # noqa: E402

BASE_URL = "https://shop.chatgpt.org.uk"
EXTRA_SITES = [
    "https://shop.infiniteai.cc",
    "https://ldc-shop.einzieg.site",
    "https://ldc-shop.lwb13145200.workers.dev",
    "https://shop.catsapi.com",
    "https://ldc-shop.nwjump.workers.dev",
    "https://shop.wcnmb.eu.org",
]
SESSION_DIR = os.path.dirname(os.path.abspath(__file__))
IMPERSONATE_TARGETS = ["chrome133a", "chrome136", "chrome142"]

# 按用户要求：写死 CDK 账号密码用于测试
HARDCODED_CDK_USERNAME = "zhou239289001@gmail.com"
HARDCODED_CDK_PASSWORD = "zhou060423rls"

# 按用户要求：使用 README 里的 TG 配置；Shop 默认直连（不走本地 10808 代理）
HARDCODED_TG_BOT_TOKEN = "7483346980:AAHT4LBRiDU0H617sRQZmNUL8A6GumybMHE"
HARDCODED_TG_CHAT_ID = "7420206850"
HARDCODED_PROXY = ""

# 可选：写死 LinuxDo 已登录 Cookie（用于“跳过账号密码登录”）
# 形如: "_forum_session=xxx; __cf_bm=xxx"
HARDCODED_LINUXDO_COOKIE = ""

# 从线上 chunk 中可动态提取，保留默认值作为回退
DEFAULT_ACTION_GET_STATUS = "00451245bddba2b171ece2e019fdb69db9c01f7670"
DEFAULT_ACTION_CHECKIN = "009e211ee575ae422b7380507880ef4c73fb4f634a"
DEFAULT_ACTION_GET_POINTS = "00f4e080b84a66c9c03b237ac825d1122daac08c6"

# App Router 首页状态树（用于 Server Action 请求头）
ROOT_TREE = "%5B%22%22%2C%7B%22children%22%3A%5B%22__PAGE__%22%2C%7B%7D%5D%7D%2Cnull%2Cnull%2Ctrue%5D"

log = logging.getLogger("Shop签到")


def normalize_origin(url):
    try:
        p = urlparse((url or "").strip())
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
    except Exception:
        pass
    return ""


def get_site_list():
    raw = [BASE_URL] + EXTRA_SITES
    out, seen = [], set()
    for u in raw:
        n = normalize_origin(u)
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def session_file_for(base_url):
    host = (urlparse(base_url).netloc or "default").replace(":", "_")
    return os.path.join(SESSION_DIR, f"session_{host}.json")


def save_session(session_file, cookies_dict, user_info=None):
    data = {"cookies": cookies_dict, "user": user_info, "timestamp": time.time()}
    with open(session_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_session(session_file):
    if not os.path.exists(session_file):
        return None
    try:
        with open(session_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 7 天缓存
        if time.time() - data.get("timestamp", 0) > 86400 * 7:
            return None
        return data
    except Exception:
        return None


class ShopClient:
    def __init__(self, base_url=BASE_URL, proxy=None):
        self.base_url = normalize_origin(base_url) or BASE_URL
        self.proxy = proxy
        self.session, self.impersonate = create_session()

    def _switch_impersonate(self, target):
        old = []
        jar = self.session.cookies
        try:
            for c in jar.jar:
                old.append((c.name, c.value, c.domain, c.path))
        except Exception:
            if hasattr(jar, "items"):
                for k, v in jar.items():
                    old.append((k, v, None, "/"))

        self.impersonate = target
        self.session = CompatSession(impersonate=target)

        for name, value, domain, path in old:
            kw = {}
            if domain:
                kw["domain"] = domain
            if path:
                kw["path"] = path
            self.session.cookies.set(name, value, **kw)

    def _get(self, url, **kw):
        kw.setdefault("proxy", self.proxy)
        kw.setdefault("timeout", 30)
        return self.session.get(url, **kw)

    def _post(self, url, **kw):
        kw.setdefault("proxy", self.proxy)
        kw.setdefault("timeout", 30)
        return self.session.post(url, **kw)

    def export_cookies(self):
        out = []
        jar = self.session.cookies
        try:
            for c in jar.jar:
                out.append(
                    {
                        "name": c.name,
                        "value": c.value,
                        "domain": c.domain or urlparse(self.base_url).netloc,
                        "path": c.path or "/",
                    }
                )
        except Exception:
            if hasattr(jar, "items"):
                domain = urlparse(self.base_url).netloc
                for k, v in jar.items():
                    out.append({"name": k, "value": v, "domain": domain, "path": "/"})
        return out

    def restore_cookies(self, cookies_data):
        # 兼容旧格式: {"cookie_name": "value"}
        if isinstance(cookies_data, dict):
            domain = urlparse(self.base_url).netloc
            for k, v in cookies_data.items():
                self.session.cookies.set(k, v, domain=domain, path="/")
            return

        if not isinstance(cookies_data, list):
            return

        for item in cookies_data:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            value = item.get("value")
            if not name or value is None:
                continue
            domain = item.get("domain") or urlparse(self.base_url).netloc
            path = item.get("path") or "/"
            self.session.cookies.set(name, value, domain=domain, path=path)

    def set_cookie_string(self, cookie_str, domain):
        """从 'k1=v1; k2=v2' 字符串写入 cookie"""
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

    def export_cookie_string(self, domain_keyword="linux.do"):
        """
        导出 cookie 字符串（默认仅导出 linux.do 相关域）。
        说明：不能直接用 cookies.items()，在 curl_cffi 下同名多域 cookie 会触发 CookieConflict。
        """
        pairs = []
        dedup = {}

        jar = self.session.cookies
        try:
            # curl_cffi/requests 兼容：遍历底层 cookie 对象，避免 __getitem__ 冲突
            for c in jar.jar:
                domain = (getattr(c, "domain", "") or "").lower()
                name = getattr(c, "name", "")
                value = getattr(c, "value", None)
                if not name or value is None:
                    continue
                if domain_keyword and domain_keyword not in domain:
                    continue
                dedup[name] = str(value)
        except Exception:
            # 退化路径：仅在无法访问 jar.jar 时使用
            if hasattr(jar, "items"):
                for k, v in jar.items():
                    dedup[str(k)] = str(v)

        for k, v in dedup.items():
            pairs.append(f"{k}={v}")
        return "; ".join(pairs)

    def login_linuxdo(self, username, password):
        """先登录 linux.do，后续 OAuth 跳转才能直接放行"""
        for _ in range(2):
            r = self._get("https://linux.do/session/csrf.json")
            if r.status_code == 403:
                for alt in IMPERSONATE_TARGETS:
                    if alt == self.impersonate:
                        continue
                    self._switch_impersonate(alt)
                    delay(1, 2)
                    r = self._get("https://linux.do/session/csrf.json")
                    if r.status_code == 200:
                        break

            if r.status_code != 200:
                delay(1, 2)
                continue

            try:
                csrf = r.json().get("csrf")
            except Exception:
                csrf = None
            if not csrf:
                delay(1, 2)
                continue

            delay(0.5, 1.2)
            r = self._post(
                "https://linux.do/session",
                data={"login": username, "password": password, "second_factor_method": "1"},
                headers={
                    "X-CSRF-Token": csrf,
                    "X-Requested-With": "XMLHttpRequest",
                    "Origin": "https://linux.do",
                    "Referer": "https://linux.do/login",
                },
            )
            if r.status_code == 200:
                try:
                    data = r.json()
                    if data.get("user", {}).get("username"):
                        return True
                except Exception:
                    pass

            delay(1, 2)
        return False

    def prewarm_connect_session(self):
        """
        预热 connect.linux.do，会在 cookie 中写入/刷新 auth.session-token。
        """
        try:
            r = self._get(
                "https://connect.linux.do/oauth2/authorize?response_type=code&client_id=linux_do",
                allow_redirects=True,
            )
            token = self.session.cookies.get("auth.session-token")
            return bool(token) or (getattr(r, "status_code", 0) in (200, 302, 303, 401))
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
        # 常见写法: location.href = "..."; window.location = "...";
        for pat in [
            r'location\.href\s*=\s*["\']([^"\']+)["\']',
            r'window\.location\s*=\s*["\']([^"\']+)["\']',
            r'window\.location\.replace\(["\']([^"\']+)["\']\)',
        ]:
            m = re.search(pat, html or "", re.I)
            if m:
                return self._abs_url(current_url, m.group(1).strip())
        return ""

    def _follow_oauth_flow(self, url, max_hops=30):
        r = self._get(url, allow_redirects=False)
        hop = 0
        current = url

        while hop < max_hops:
            final_url = str(getattr(r, "url", "") or current)

            if r.status_code in (301, 302, 303, 307, 308):
                loc = self._abs_url(final_url, r.headers.get("location", ""))
                if not loc:
                    return r

                # 命中 NextAuth callback 时直接 allow_redirects=True，确保会话 cookie 写入
                if "/api/auth/callback/linuxdo" in loc:
                    return self._get(loc, allow_redirects=True)

                r = self._get(loc, allow_redirects=False)
                current = loc
                hop += 1
                continue

            if r.status_code == 200 and "connect.linux.do" in final_url:
                approve = re.search(r'href="(/oauth2/approve/[^"]+)"', r.text or "")
                if approve:
                    approve_url = self._abs_url(final_url, approve.group(1))
                    r = self._get(approve_url, allow_redirects=False)
                    current = approve_url
                    hop += 1
                    continue

                refresh_url = self._extract_meta_refresh(r.text, final_url)
                if refresh_url:
                    r = self._get(refresh_url, allow_redirects=False)
                    current = refresh_url
                    hop += 1
                    continue

                js_url = self._extract_js_redirect(r.text, final_url)
                if js_url:
                    r = self._get(js_url, allow_redirects=False)
                    current = js_url
                    hop += 1
                    continue

            return r

        return r

    def login_shop(self):
        # 1) NextAuth CSRF
        r = self._get(f"{self.base_url}/api/auth/csrf")
        if r.status_code != 200:
            log.error("shop csrf 失败(%s): %s", self.base_url, r.status_code)
            return False
        try:
            csrf_token = r.json().get("csrfToken")
        except Exception:
            return False
        if not csrf_token:
            return False

        # 2) 发起 signin/linuxdo
        delay(1, 2)
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
            auth_url = r.json().get("url", "")
        except Exception:
            pass
        if not auth_url:
            auth_url = r.headers.get("location", "")
        if not auth_url:
            log.error("未获取到 OAuth authorize URL: %s", self.base_url)
            return False

        # 3) 跟随 OAuth，处理 approve/meta/js/callback
        self._follow_oauth_flow(auth_url)

        # 4) 首次验证 session
        user = self.verify_login()
        if user:
            return True

        # 5) 对齐其它脚本容错：补一次 prompt=none 的静默授权
        p = urlparse(auth_url)
        if p.netloc == "connect.linux.do":
            q = dict(parse_qsl(p.query, keep_blank_values=True))
            if "prompt" not in q:
                q["prompt"] = "none"
                silent_url = urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q), p.fragment))
                delay(1, 2)
                self._follow_oauth_flow(silent_url)
                user = self.verify_login()
                if user:
                    return True

        # 6) NextAuth 会话落盘偶发延迟，短暂重试
        for _ in range(2):
            delay(1, 2)
            user = self.verify_login()
            if user:
                return True

        return False

    def verify_login(self):
        r = self._get(f"{self.base_url}/api/auth/session")
        if r.status_code != 200:
            return None
        try:
            data = r.json()
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        user = data.get("user")
        if isinstance(user, dict) and (user.get("id") or user.get("name") or user.get("username")):
            return user
        return None

    def _find_action_id(self, chunk_text, action_name):
        # 兼容两种打包形态：
        # 1) createServerReference("hash", ..., "fnName")
        # 2) (0,n.createServerReference)("hash", ..., "fnName")
        escaped = re.escape(action_name)
        patterns = [
            rf'createServerReference\("([a-f0-9]{{40,64}})"[^)]{{0,320}}"{escaped}"\)',
            rf'createServerReference\)\("([a-f0-9]{{40,64}})"[^)]{{0,360}}"{escaped}"\)',
            rf'"([a-f0-9]{{40,64}})".{{0,220}}"{escaped}"',
        ]
        for pat in patterns:
            m = re.search(pat, chunk_text, re.S)
            if m:
                return m.group(1)
        return None

    def fetch_action_ids(self):
        action_status = None
        action_checkin = None
        action_points = None

        r = self._get(f"{self.base_url}/")
        if r.status_code != 200:
            return DEFAULT_ACTION_GET_STATUS, DEFAULT_ACTION_CHECKIN, DEFAULT_ACTION_GET_POINTS

        chunk_paths = re.findall(r'<script[^>]+src="([^"]*/_next/static/chunks/[^"]+\.js)"', r.text, re.I)
        seen = set()

        for chunk_path in chunk_paths:
            chunk_url = chunk_path
            if chunk_url.startswith("/"):
                chunk_url = f"{self.base_url}{chunk_url}"
            if chunk_url in seen:
                continue
            seen.add(chunk_url)

            jr = self._get(chunk_url)
            if jr.status_code != 200:
                continue

            txt = jr.text
            if not action_status:
                action_status = self._find_action_id(txt, "getCheckinStatus")
            if not action_checkin:
                action_checkin = self._find_action_id(txt, "checkIn")
            if not action_points:
                action_points = self._find_action_id(txt, "getUserPoints")

            if action_status and action_checkin and action_points:
                break

        return (
            action_status or DEFAULT_ACTION_GET_STATUS,
            action_checkin or DEFAULT_ACTION_CHECKIN,
            action_points or DEFAULT_ACTION_GET_POINTS,
        )

    def _extract_json_payloads(self, text):
        out = []
        for line in (text or "").splitlines():
            if ":" not in line:
                continue
            _, payload = line.split(":", 1)
            payload = payload.strip()
            if not payload:
                continue
            try:
                out.append(json.loads(payload))
            except Exception:
                pass
        return out

    def _find_result_obj(self, node):
        if isinstance(node, dict):
            if "checkedIn" in node:
                return node
            if "success" in node and ("points" in node or "error" in node or "message" in node):
                return node
            for v in node.values():
                found = self._find_result_obj(v)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = self._find_result_obj(item)
                if found is not None:
                    return found
        return None

    def call_server_action(self, action_id, args=None):
        if args is None:
            args = []

        headers = {
            "Next-Action": action_id,
            "Accept": "text/x-component",
            "Next-Router-State-Tree": ROOT_TREE,
            "Content-Type": "text/plain;charset=UTF-8",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/",
        }

        r = self._post(f"{self.base_url}/", headers=headers, data=json.dumps(args))
        if r.status_code != 200:
            return {"ok": False, "http": r.status_code, "raw": r.text[:300]}

        payloads = self._extract_json_payloads(r.text or "")
        result = None

        for payload in payloads:
            found = self._find_result_obj(payload)
            if found is not None:
                result = found
                break

        if result is None and payloads:
            for payload in payloads:
                if isinstance(payload, dict) and set(payload.keys()).issubset({"a", "f", "b", "q", "i"}):
                    continue
                result = payload
                break

        if result is None:
            # 兜底：文本中直接匹配
            txt = r.text or ""
            if "Already checked in today" in txt or "今天已签到" in txt:
                result = {"success": False, "error": "Already checked in today"}
            elif "checkedIn" in txt:
                result = {"checkedIn": True}
            else:
                result = {"raw": txt[:300]}
        return {"ok": True, "data": result, "raw": r.text[:300]}

    def get_checkin_status(self, action_get_status):
        res = self.call_server_action(action_get_status, [])
        if not res.get("ok"):
            return {"checkedIn": False, "error": f"HTTP {res.get('http')}"}
        data = res.get("data", {})
        if isinstance(data, dict) and "checkedIn" in data:
            return data
        return {"checkedIn": False}

    def checkin(self, action_checkin):
        res = self.call_server_action(action_checkin, [])
        if not res.get("ok"):
            return {"success": False, "error": f"HTTP {res.get('http')}"}
        data = res.get("data", {})
        if isinstance(data, dict):
            if "success" in data:
                return data
            if data.get("checkedIn") is True:
                return {"success": False, "error": "Already checked in today"}
            if data.get("checkedIn") is False:
                return {"success": False, "error": data.get("error") or data.get("message") or str(data)}
        if isinstance(data, (int, float)):
            return {"success": True, "points": int(data)}
        return {"success": False, "error": str(data)}

    def _get_user_points_from_profile(self):
        """兜底：从 /profile 页面解析积分（用户提供的 DOM 结构）"""
        r = self._get(f"{self.base_url}/profile", allow_redirects=True)
        if r.status_code != 200:
            return None

        html = r.text or ""

        # 优先匹配“积分/Points/Credits + 数字”的相邻结构
        patterns = [
            r'>\s*(?:积分|Points|Credits)\s*</p>\s*<p[^>]*>\s*([0-9]+)\s*</p>',
            r'<p[^>]*>\s*(?:积分|Points|Credits)\s*</p>\s*<p[^>]*>\s*([0-9]+)\s*</p>',
        ]
        for pat in patterns:
            m = re.search(pat, html, re.I)
            if m:
                try:
                    return int(m.group(1))
                except Exception:
                    pass

        # 兜底：profile 页面中常见的 points 字段
        m = re.search(r'"points"\s*:\s*([0-9]+)', html, re.I)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                pass

        return None

    def get_user_points(self, action_points):
        # 先尝试 Server Action（快、稳定时优先）
        if action_points:
            res = self.call_server_action(action_points, [])
            if res.get("ok"):
                data = res.get("data")
                if isinstance(data, (int, float)):
                    return int(data)
                if isinstance(data, str) and data.isdigit():
                    return int(data)
            else:
                log.warning("getUserPoints action 失败: http=%s, raw=%s", res.get("http"), (res.get("raw") or "")[:120])

        # 回退：从 profile 页面解析积分
        return self._get_user_points_from_profile()

    def _strip_html(self, text):
        t = re.sub(r"<[^>]+>", "", text or "")
        t = html_lib.unescape(t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    def _is_noise_product_name(self, name):
        n = (name or "").strip().lower()
        if len(n) < 2:
            return True
        noise_words = [
            "签到", "登录", "shop", "search", "profile", "loading",
            "__next", "undefined", "null"
        ]
        return any(w in n for w in noise_words)

    def _extract_price_text(self, text):
        if not text:
            return ""
        for pat in [
            r'"(?:price|credits|points|cost|amount)"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)"?',
            r'(?:price|credits|points|积分|价格|售价|cost|amount)\s*["\':： ]{0,10}([0-9]+(?:\.[0-9]+)?)',
            r'([0-9]+(?:\.[0-9]+)?)\s*(?:Credits|积分|points?)',
        ]:
            m = re.search(pat, text, re.I | re.S)
            if m:
                return m.group(1)
        return ""

    def _try_add_product(self, out, seen, name, price=""):
        name = self._strip_html(name or "")
        if not name or self._is_noise_product_name(name):
            return

        key = name.lower()
        if key in seen:
            return
        seen.add(key)

        price_text = self._extract_price_text(str(price) if price is not None else "")
        out.append({"name": name, "price": price_text})

    def _collect_products_from_json(self, node, out, seen, limit):
        if len(out) >= limit:
            return

        if isinstance(node, dict):
            name = (
                node.get("name")
                or node.get("title")
                or node.get("productName")
                or node.get("goodsName")
            )
            price = (
                node.get("price")
                or node.get("credits")
                or node.get("points")
                or node.get("cost")
                or node.get("amount")
            )

            offers = node.get("offers")
            if not price and isinstance(offers, dict):
                price = offers.get("price") or offers.get("amount")

            if name:
                self._try_add_product(out, seen, str(name), price)

            for v in node.values():
                self._collect_products_from_json(v, out, seen, limit)
                if len(out) >= limit:
                    return

        elif isinstance(node, list):
            for item in node:
                self._collect_products_from_json(item, out, seen, limit)
                if len(out) >= limit:
                    return

    def _extract_products_from_text(self, text, out, seen, limit):
        if not text or len(out) >= limit:
            return

        candidates = [text]
        if '\\"' in text:
            candidates.append(text.replace('\\"', '"'))
        if "\\u" in text:
            try:
                candidates.append(bytes(text, "utf-8").decode("unicode_escape"))
            except Exception:
                pass

        for src in candidates:
            # 1) name/title + price 紧邻
            for m in re.finditer(
                r'"(?:name|title|productName|goodsName)"\s*:\s*"([^"]{2,180})"(.{0,260}?)"(?:price|credits|points|cost|amount)"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)"?',
                src,
                re.I | re.S,
            ):
                self._try_add_product(out, seen, m.group(1), m.group(3))
                if len(out) >= limit:
                    return

            # 2) buy 链接卡片
            for block in re.findall(r'<a[^>]+href=["\']/buy/[^"\']+["\'][^>]*>.*?</a>', src, re.I | re.S):
                m_name = re.search(r'<h[1-6][^>]*>(.*?)</h[1-6]>', block, re.I | re.S)
                if not m_name:
                    m_name = re.search(r'>([^<>]{2,80})<', block)
                if not m_name:
                    continue
                self._try_add_product(out, seen, m_name.group(1), self._extract_price_text(block))
                if len(out) >= limit:
                    return

            # 3) name-only + 邻域 price
            for m in re.finditer(r'"(?:name|title|productName|goodsName)"\s*:\s*"([^"]{2,180})"', src, re.I):
                name = m.group(1)
                nearby = src[m.end(): m.end() + 220]
                self._try_add_product(out, seen, name, self._extract_price_text(nearby))
                if len(out) >= limit:
                    return

    def get_sale_products(self, limit=8):
        pages = [
            f"{self.base_url}/",
            f"{self.base_url}/search",
            f"{self.base_url}/market",
            f"{self.base_url}/buy",
        ]
        out = []
        seen = set()

        # A. 页面抓取（HTML / RSC 文本）
        for page in pages:
            r = self._get(page, allow_redirects=True)
            if r.status_code != 200:
                continue

            html = r.text or ""
            self._extract_products_from_text(html, out, seen, limit)
            if len(out) >= limit:
                return out

            # 进一步解析内嵌 script（很多站点把商品塞在 script JSON/RSC 中）
            for script_text in re.findall(r"<script[^>]*>(.*?)</script>", html, re.I | re.S):
                if not script_text:
                    continue
                self._extract_products_from_text(script_text, out, seen, limit)
                if len(out) >= limit:
                    return out

        # B. API 兜底（不同克隆站命名可能不同）
        api_candidates = [
            "/api/products",
            "/api/products/list",
            "/api/shop/products",
            "/api/store/products",
            "/api/search",
            "/api/search?query=",
        ]
        for ep in api_candidates:
            r = self._get(f"{self.base_url}{ep}", allow_redirects=True)
            if r.status_code != 200:
                continue

            body = r.text or ""
            ctype = (r.headers.get("content-type", "") if getattr(r, "headers", None) else "").lower()

            if "json" in ctype or body.strip().startswith("{") or body.strip().startswith("["):
                try:
                    data = r.json()
                    self._collect_products_from_json(data, out, seen, limit)
                except Exception:
                    self._extract_products_from_text(body, out, seen, limit)
            else:
                self._extract_products_from_text(body, out, seen, limit)

            if len(out) >= limit:
                return out

        return out


def build_msg(site_url, user, result, status=None, current_points=None, checkin_gain=None, products=None):
    uname = (user or {}).get("username") or (user or {}).get("name") or "未知"
    lines = ["🛍️ Shop 签到", f"🌐 网站: {site_url}", f"用户: {uname}"]

    if status and status.get("checkedIn") is True and not result:
        lines.append("✅ 今日已签到")
    else:
        success = bool(result and result.get("success"))
        if success:
            points = result.get("points", 0)
            lines.append(f"✅ 签到成功 (+{points} 积分)")
        else:
            err = (result or {}).get("error") or (result or {}).get("message") or "未知错误"
            if "Already checked in today" in str(err):
                lines.append("✅ 今日已签到")
            else:
                lines.append(f"⚠️ 签到失败: {err}")

    if isinstance(checkin_gain, (int, float)):
        lines.append(f"🎁 本次签到积分: +{int(checkin_gain)}")
    else:
        lines.append("🎁 本次签到积分: 未知")

    if isinstance(current_points, (int, float)):
        lines.append(f"🪙 当前积分: {int(current_points)}")
    else:
        lines.append("🪙 当前积分: 未知")

    if products:
        lines.append("🛒 在售商品:")
        for p in products[:8]:
            if isinstance(p, dict):
                name = (p.get("name") or "").strip() or "未知商品"
                price = str(p.get("price") or "").strip()
                if price:
                    lines.append(f"- {name} | 价格: {price} 积分")
                else:
                    lines.append(f"- {name} | 价格: 未知")
            else:
                lines.append(f"- {p} | 价格: 未知")
    else:
        lines.append("🛒 在售商品: 未获取到")

    return "\n".join(lines)


def _inject_linuxdo_cookie(client, linuxdo_cookie):
    if not linuxdo_cookie:
        return
    client.set_cookie_string(linuxdo_cookie, "linux.do")
    client.set_cookie_string(linuxdo_cookie, ".linux.do")
    client.set_cookie_string(linuxdo_cookie, "connect.linux.do")


def run_one_site(site_url, username, password, proxy, linuxdo_cookie=""):
    site_url = normalize_origin(site_url) or BASE_URL
    session_file = session_file_for(site_url)
    client = ShopClient(base_url=site_url, proxy=proxy)

    _inject_linuxdo_cookie(client, linuxdo_cookie)

    products = client.get_sale_products(limit=8)

    # 1) 尝试复用该站点会话
    saved = load_session(session_file)
    if saved and saved.get("cookies"):
        client.restore_cookies(saved["cookies"])
        user = client.verify_login()
        if user:
            action_status, action_checkin, action_points = client.fetch_action_ids()
            status = client.get_checkin_status(action_status)
            if status.get("checkedIn") is True:
                current_points = client.get_user_points(action_points)
                msg = build_msg(site_url, user, None, status, current_points=current_points, checkin_gain=0, products=products)
                return True, msg, client.export_cookie_string()

            result = client.checkin(action_checkin)
            gain = int(result.get("points", 0)) if isinstance(result, dict) and result.get("success") else 0
            current_points = client.get_user_points(action_points)
            ok = bool(result.get("success")) or ("Already checked in today" in str(result.get("error")))
            msg = build_msg(site_url, user, result, status, current_points=current_points, checkin_gain=gain, products=products)
            return ok, msg, client.export_cookie_string()

    # 2) 先尝试仅凭 linuxdo cookie 登录
    client.prewarm_connect_session()
    if not client.login_shop():
        # 3) 回退：显式 LinuxDo 账号密码登录，再走站点 OAuth
        if not client.login_linuxdo(username, password):
            msg = build_msg(
                site_url,
                None,
                {"success": False, "error": "LinuxDo 登录失败"},
                None,
                current_points=None,
                checkin_gain=None,
                products=products,
            )
            return False, msg, client.export_cookie_string()

        client.prewarm_connect_session()
        if not client.login_shop():
            msg = build_msg(
                site_url,
                None,
                {"success": False, "error": "Shop OAuth 登录失败"},
                None,
                current_points=None,
                checkin_gain=None,
                products=products,
            )
            return False, msg, client.export_cookie_string()

    user = client.verify_login()
    if not user:
        msg = build_msg(
            site_url,
            None,
            {"success": False, "error": "登录后会话验证失败"},
            None,
            current_points=None,
            checkin_gain=None,
            products=products,
        )
        return False, msg, client.export_cookie_string()

    save_session(session_file, client.export_cookies(), user)

    if not products:
        products = client.get_sale_products(limit=8)

    action_status, action_checkin, action_points = client.fetch_action_ids()
    status = client.get_checkin_status(action_status)
    if status.get("checkedIn") is True:
        current_points = client.get_user_points(action_points)
        msg = build_msg(site_url, user, None, status, current_points=current_points, checkin_gain=0, products=products)
        return True, msg, client.export_cookie_string()

    result = client.checkin(action_checkin)
    gain = int(result.get("points", 0)) if isinstance(result, dict) and result.get("success") else 0
    current_points = client.get_user_points(action_points)
    ok = bool(result.get("success")) or ("Already checked in today" in str(result.get("error")))
    msg = build_msg(site_url, user, result, status, current_points=current_points, checkin_gain=gain, products=products)
    return ok, msg, client.export_cookie_string()


def run(send_tg=None):
    """执行多站点签到，返回 (整体成功?, 汇总消息)"""
    username = HARDCODED_CDK_USERNAME
    password = HARDCODED_CDK_PASSWORD
    # 默认直连；仅在显式设置 SHOP_PROXY（或代码里 HARDCODED_PROXY）时才走代理
    proxy = (os.environ.get("SHOP_PROXY", "").strip() or HARDCODED_PROXY.strip() or None)

    linuxdo_cookie = (os.environ.get("LINUXDO_COOKIE", "").strip() or HARDCODED_LINUXDO_COOKIE.strip())
    if not linuxdo_cookie and username and password:
        bootstrap = ShopClient(base_url=BASE_URL, proxy=proxy)
        if bootstrap.login_linuxdo(username, password):
            bootstrap.prewarm_connect_session()
            linuxdo_cookie = bootstrap.export_cookie_string()

    rows = []
    sites = get_site_list()

    for idx, site in enumerate(sites, 1):
        ok, msg, fresh_cookie = run_one_site(site, username, password, proxy, linuxdo_cookie=linuxdo_cookie)
        if fresh_cookie:
            linuxdo_cookie = fresh_cookie

        rows.append((site, ok))
        log.info("[%s/%s] %s %s", idx, len(sites), "✅" if ok else "❌", site)

        # 核心需求：每签到一个网站都推送一次 TG
        if send_tg:
            send_tg(msg)

        delay(0.5, 1.0)

    success = sum(1 for _, ok in rows if ok)
    summary_lines = [
        "🛍️ Shop 多站点签到汇总",
        f"总站点: {len(rows)}",
        f"成功: {success}",
        f"失败: {len(rows) - success}",
    ]
    for site, ok in rows:
        summary_lines.append(f"{'✅' if ok else '❌'} {site}")

    return success == len(rows), "\n".join(summary_lines)


def main():
    load_env()
    from utils import send_telegram
    os.environ["TG_BOT_TOKEN"] = HARDCODED_TG_BOT_TOKEN
    os.environ["TG_CHAT_ID"] = HARDCODED_TG_CHAT_ID
    ok, msg = run(send_tg=send_telegram)
    log.info(msg)


if __name__ == "__main__":
    main()