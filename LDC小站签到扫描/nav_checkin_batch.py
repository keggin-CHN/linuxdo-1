"""批量对 nav_checkin_sites.txt 中站点执行签到，并把成功站点写入 TXT。"""
import json
import logging
import os
import re
import sys
import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT_DIR)

from utils import CompatSession, create_session, delay, get_proxy, load_env  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("nav_checkin_batch")

IMPERSONATE_TARGETS = ["chrome133a", "chrome136", "chrome142"]
ROOT_TREE = "%5B%22%22%2C%7B%22children%22%3A%5B%22__PAGE__%22%2C%7B%7D%5D%7D%2Cnull%2Cnull%2Ctrue%5D"
DEFAULT_ACTION_GET_STATUS = "00451245bddba2b171ece2e019fdb69db9c01f7670"
DEFAULT_ACTION_CHECKIN = "009e211ee575ae422b7380507880ef4c73fb4f634a"

HARDCODED_CDK_USERNAME = "zhou239289001@gmail.com"
HARDCODED_CDK_PASSWORD = "zhou060423rls"
HARDCODED_PROXY = ""

DEFAULT_SITES_FILE = os.path.join(ROOT_DIR, "nav_checkin_sites.txt")
DEFAULT_SUCCESS_FILE = os.path.join(ROOT_DIR, "nav_checkin_success_sites.txt")
DEFAULT_RESULT_FILE = os.path.join(ROOT_DIR, "nav_checkin_result.json")
DEFAULT_MAX_SITES = 0
DEFAULT_REQUEST_TIMEOUT = 30
DEFAULT_SHOP_SESSION_FILE = os.path.join(ROOT_DIR, "shop", "session.json")

REQUEST_TIMEOUT = DEFAULT_REQUEST_TIMEOUT


def normalize_origin(url: str) -> str:
    try:
        p = urlparse(url.strip())
        if p.scheme != "https" or not p.netloc:
            return ""
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


def read_sites(path: str):
    if not os.path.exists(path):
        raise FileNotFoundError(f"站点文件不存在: {path}")

    seen = set()
    sites = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            u = normalize_origin(line.strip())
            if not u or u in seen:
                continue
            seen.add(u)
            sites.append(u)
    return sites


def load_linuxdo_cookie_from_shop_session(session_file: str = DEFAULT_SHOP_SESSION_FILE):
    """优先复用 shop/session.json 里的 linux.do 登录态 cookie。"""
    if not os.path.exists(session_file):
        return ""

    try:
        with open(session_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return ""

    cookies = data.get("cookies")
    pairs = []

    # 新格式: [{"name": "...", "value": "...", "domain": "..."}]
    if isinstance(cookies, list):
        for item in cookies:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            value = item.get("value")
            domain = str(item.get("domain", "")).strip().lower()
            if not name or value is None:
                continue
            if "linux.do" in domain:
                pairs.append((name, str(value)))

    # 旧格式: {"cookie_name": "value"}，无法识别域名，按常见 linux.do 会话键兜底
    elif isinstance(cookies, dict):
        allow_names = {"_forum_session", "_t", "auth.session-token"}
        for k, v in cookies.items():
            name = str(k).strip()
            if not name or v is None:
                continue
            if (name in allow_names) or ("linux" in name.lower()):
                pairs.append((name, str(v)))

    # 去重（同名保留最后一个）
    if not pairs:
        return ""
    dedup = {}
    for k, v in pairs:
        dedup[k] = v
    return "; ".join(f"{k}={v}" for k, v in dedup.items())


class SiteCheckinClient:
    def __init__(self, base_url: str, proxy: str = None):
        self.base_url = normalize_origin(base_url)
        if not self.base_url:
            raise ValueError(f"非法站点 URL: {base_url}")
        self.proxy = proxy
        self.session, self.impersonate = create_session()

    def _cookie_set(self, name: str, value: str, domain: str = None, path: str = "/"):
        try:
            if domain:
                self.session.cookies.set(name, value, domain=domain, path=path)
            else:
                self.session.cookies.set(name, value)
        except TypeError:
            if domain:
                self.session.cookies.set(name, value, domain=domain)
            else:
                self.session.cookies.set(name, value)

    def _switch_impersonate(self, target: str):
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
            self._cookie_set(name, value, domain=domain, path=path or "/")

    def _get(self, url, **kw):
        kw.setdefault("proxy", self.proxy)
        kw.setdefault("timeout", REQUEST_TIMEOUT)
        return self.session.get(url, **kw)

    def _post(self, url, **kw):
        kw.setdefault("proxy", self.proxy)
        kw.setdefault("timeout", REQUEST_TIMEOUT)
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
                self._cookie_set(name, value, domain=domain)

    def export_cookie_string(self):
        pairs = []
        if hasattr(self.session.cookies, "items"):
            for k, v in self.session.cookies.items():
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
        # 增强稳定性：最多尝试 2 轮，每轮内支持指纹切换
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
                csrf = (r.json() or {}).get("csrf")
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
            if r.status_code != 200:
                delay(1, 2)
                continue

            try:
                data = r.json()
                if bool(isinstance(data, dict) and data.get("user", {}).get("username")):
                    return True
            except Exception:
                pass

            delay(1, 2)

        return False

    def prewarm_connect_session(self):
        """
        预热 connect.linux.do，会在 cookie 中写入/刷新 auth.session-token。
        实测即使返回 401，只要 cookie 写入成功，也能显著提升后续 OAuth 成功率。
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

    def _abs_url(self, current_url: str, loc: str):
        if not loc:
            return ""
        if loc.startswith("/"):
            p = urlparse(current_url)
            return f"{p.scheme}://{p.netloc}{loc}"
        return loc

    def _extract_meta_refresh(self, html: str, current_url: str):
        m = re.search(r'http-equiv=["\']refresh["\'][^>]*url=([^"\'>\s]+)', html or "", re.I)
        if not m:
            return ""
        return self._abs_url(current_url, m.group(1).strip())

    def _extract_js_redirect(self, html: str, current_url: str):
        for pat in [
            r'location\.href\s*=\s*["\']([^"\']+)["\']',
            r'window\.location\s*=\s*["\']([^"\']+)["\']',
            r'window\.location\.replace\(["\']([^"\']+)["\']\)',
        ]:
            m = re.search(pat, html or "", re.I)
            if m:
                return self._abs_url(current_url, m.group(1).strip())
        return ""

    def _follow_oauth_flow(self, url: str, max_hops: int = 30):
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
                m = re.search(r'href="(/oauth2/approve/[^"]+)"', r.text or "")
                if m:
                    u = self._abs_url(final_url, m.group(1))
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
        """目标站 OAuth 登录，遇到 CF 403 时自动切换指纹重试。"""
        tried = set()
        candidates = [self.impersonate] + [x for x in IMPERSONATE_TARGETS if x != self.impersonate]

        for imp in candidates:
            if imp in tried:
                continue
            tried.add(imp)

            if imp != self.impersonate:
                self._switch_impersonate(imp)
                delay(0.8, 1.5)

            r = self._get(f"{self.base_url}/api/auth/csrf")
            if r.status_code != 200:
                continue

            csrf_token = (r.json() or {}).get("csrfToken")
            if not csrf_token:
                continue

            delay(0.4, 1.0)
            r = self._post(
                f"{self.base_url}/api/auth/signin/linuxdo",
                data={"csrfToken": csrf_token, "callbackUrl": self.base_url, "json": "true"},
                headers={"Content-Type": "application/x-www-form-urlencoded", "X-Auth-Return-Redirect": "1"},
                allow_redirects=False,
            )

            auth_url = ""
            try:
                auth_url = (r.json() or {}).get("url", "")
            except Exception:
                pass
            if not auth_url:
                auth_url = r.headers.get("location", "")
            if not auth_url:
                continue

            rr = self._follow_oauth_flow(auth_url)
            if self.verify_login():
                return True

            # prompt=none 再试一次
            p = urlparse(auth_url)
            if p.netloc == "connect.linux.do":
                q = dict(parse_qsl(p.query, keep_blank_values=True))
                if "prompt" not in q:
                    q["prompt"] = "none"
                    silent_url = urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q), p.fragment))
                    rr = self._follow_oauth_flow(silent_url)
                    if self.verify_login():
                        return True

            # Cloudflare challenge: Just a moment...
            txt = (getattr(rr, "text", "") or "")[:400]
            if getattr(rr, "status_code", 0) == 403 and "Just a moment" in txt:
                continue

        return False

    def _find_action_id(self, chunk_text: str, action_name: str):
        escaped = re.escape(action_name)

        # 先做“同一调用内”的精确匹配，避免跨调用误捕获
        strict_patterns = [
            rf'createServerReference\("([a-f0-9]{{40,64}})"[^)]{{0,320}}"{escaped}"\)',
            rf'createServerReference\)\("([a-f0-9]{{40,64}})"[^)]{{0,360}}"{escaped}"\)',
        ]
        for pat in strict_patterns:
            m = re.search(pat, chunk_text)
            if m:
                return m.group(1)

        # 回退：就近匹配（限制窗口，避免跨太远）
        m = re.search(rf'"([a-f0-9]{{40,64}})".{{0,220}}"{escaped}"', chunk_text, re.S)
        if m:
            return m.group(1)

        return None

    def _collect_action_candidates(self, chunk_text: str):
        """
        回退候选提取：从相关 chunk 抽取所有 40 位十六进制 hash。
        """
        t = chunk_text or ""
        if ("checkIn" not in t) and ("getCheckinStatus" not in t) and ("createServerReference" not in t):
            return []
        return re.findall(r'"([a-f0-9]{40,64})"', t)

    def _probe_action_ids(self, candidates):
        """
        通过真实调用探测 action id，兼容多种打包形态。
        """
        status_id = None
        checkin_id = None
        seen = set()

        for aid in candidates[:120]:
            if aid in seen:
                continue
            seen.add(aid)

            res = self.call_server_action(aid, [])
            if not res.get("ok"):
                continue

            data = res.get("data")
            txt = str(data)

            if isinstance(data, dict) and "checkedIn" in data and not status_id:
                status_id = aid

            if isinstance(data, dict) and (
                ("success" in data)
                or ("Already checked in today" in txt)
                or ("今天已签到" in txt)
            ):
                if not checkin_id:
                    checkin_id = aid

            if status_id and checkin_id:
                break

        return status_id, checkin_id

    def fetch_action_ids(self):
        action_status = None
        action_checkin = None
        r = self._get(f"{self.base_url}/")
        if r.status_code != 200:
            return DEFAULT_ACTION_GET_STATUS, DEFAULT_ACTION_CHECKIN

        chunk_paths = re.findall(r'<script[^>]+src="([^"]*/_next/static/chunks/[^"]+\.js)"', r.text, re.I)
        seen = set()
        candidates = []

        for cp in chunk_paths:
            u = cp if cp.startswith("http") else f"{self.base_url}{cp}"
            if u in seen:
                continue
            seen.add(u)

            jr = self._get(u)
            if jr.status_code != 200:
                continue
            t = jr.text or ""

            # 优先：按函数名直接提取
            if not action_status:
                action_status = self._find_action_id(t, "getCheckinStatus")
            if not action_checkin:
                action_checkin = self._find_action_id(t, "checkIn")

            # 回退候选
            if not (action_status and action_checkin):
                candidates.extend(self._collect_action_candidates(t))

            if action_status and action_checkin:
                break

        # 若仍缺失，做探测识别（避免默认 hash 导致 HTTP 404）
        if not action_status or not action_checkin:
            p_status, p_checkin = self._probe_action_ids(candidates)
            action_status = action_status or p_status
            action_checkin = action_checkin or p_checkin

        return action_status or DEFAULT_ACTION_GET_STATUS, action_checkin or DEFAULT_ACTION_CHECKIN

    def _extract_json_payloads(self, text: str):
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

    def call_server_action(self, action_id: str, args=None):
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
            return {"ok": False, "http": r.status_code, "raw": (r.text or "")[:300]}

        payloads = self._extract_json_payloads(r.text or "")
        result = None

        # 1) 先从每个 payload 中递归提取业务结果
        for p in payloads:
            found = self._find_result_obj(p)
            if found is not None:
                result = found
                break

        # 2) 若仍未命中，取第一个“非框架壳层”payload（避免只拿到 0:{"a":"$@1"...}）
        if result is None and payloads:
            for p in payloads:
                if isinstance(p, dict) and set(p.keys()).issubset({"a", "f", "b", "q", "i"}):
                    continue
                result = p
                break

        if result is None:
            txt = r.text or ""
            if "Already checked in today" in txt or "今天已签到" in txt:
                result = {"success": False, "error": "Already checked in today"}
            elif "checkedIn" in txt:
                result = {"checkedIn": True}
            else:
                result = {"raw": txt[:300]}
        return {"ok": True, "data": result}

    def get_checkin_status(self, action_get_status: str):
        res = self.call_server_action(action_get_status, [])
        if not res.get("ok"):
            return {"checkedIn": False, "error": f"HTTP {res.get('http')}"}
        data = res.get("data", {})
        if isinstance(data, dict) and "checkedIn" in data:
            return data
        return {"checkedIn": False}

    def checkin(self, action_checkin: str):
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

        # 部分站点 checkIn 直接返回数字（如积分/计数）
        if isinstance(data, (int, float)):
            return {"success": True, "points": int(data)}

        return {"success": False, "error": str(data)}


def bootstrap_linuxdo_cookie(proxy: str, username: str, password: str):
    """
    获取 linux.do cookie 的优先级：
    1) 使用账号密码显式登录 linux.do，并预热 connect 会话（推荐）
    2) 回退复用 shop/session.json
    """
    from_shop = load_linuxdo_cookie_from_shop_session()

    c = SiteCheckinClient("https://shop.chatgpt.org.uk", proxy=proxy)

    # 用 shop 历史 cookie 作为 seed，提升 linux.do 登录成功率
    if from_shop:
        c.set_cookie_string(from_shop, "linux.do")
        c.set_cookie_string(from_shop, ".linux.do")
        c.set_cookie_string(from_shop, "connect.linux.do")

    if username and password and c.login_linuxdo(username, password):
        c.prewarm_connect_session()
        return c.export_cookie_string()

    if from_shop:
        return from_shop
    return ""


def run_one_site(site_url: str, proxy: str, username: str, password: str, linuxdo_cookie: str):
    out = {"url": site_url, "success": False, "already": False, "error": ""}
    try:
        c = SiteCheckinClient(site_url, proxy=proxy)
        if linuxdo_cookie:
            c.set_cookie_string(linuxdo_cookie, "linux.do")
            c.set_cookie_string(linuxdo_cookie, ".linux.do")
            c.set_cookie_string(linuxdo_cookie, "connect.linux.do")

        login_ok = False
        for _ in range(2):
            c.prewarm_connect_session()
            if c.login_target():
                login_ok = True
                break

            if username and password and c.login_linuxdo(username, password):
                delay(0.6, 1.2)
                c.prewarm_connect_session()
                if c.login_target():
                    login_ok = True
                    break

            delay(0.8, 1.6)

        if not login_ok and not c.verify_login():
            out["error"] = "login_failed"
            return out

        action_status, action_checkin = c.fetch_action_ids()
        status = c.get_checkin_status(action_status)
        if status.get("checkedIn") is True:
            out["success"] = True
            out["already"] = True
            return out

        result = c.checkin(action_checkin)
        err = str(result.get("error", ""))
        out["already"] = "Already checked in today" in err
        out["success"] = bool(result.get("success")) or out["already"]
        if not out["success"]:
            out["error"] = err or str(result)
        return out
    except Exception as e:
        out["error"] = str(e)
        return out


def save_outputs(results, success_file: str, result_file: str):
    success_sites = sorted({r["url"] for r in results if r.get("success")})

    with open(success_file, "w", encoding="utf-8") as f:
        for u in success_sites:
            f.write(u + "\n")

    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "generated_at": int(time.time()),
                "count_total": len(results),
                "count_success": len(success_sites),
                "success_file": success_file,
                "items": results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    return success_sites


def main():
    load_env()
    global REQUEST_TIMEOUT

    username = os.environ.get("CDK_USERNAME", "").strip() or HARDCODED_CDK_USERNAME
    password = os.environ.get("CDK_PASSWORD", "").strip() or HARDCODED_CDK_PASSWORD
    proxy = None

    sites_file = os.environ.get("NAV_CHECKIN_SITES_FILE", DEFAULT_SITES_FILE)
    success_file = os.environ.get("NAV_CHECKIN_SUCCESS_FILE", DEFAULT_SUCCESS_FILE)
    result_file = os.environ.get("NAV_CHECKIN_RESULT_FILE", DEFAULT_RESULT_FILE)
    max_sites = int(os.environ.get("NAV_CHECKIN_LIMIT", str(DEFAULT_MAX_SITES)))
    REQUEST_TIMEOUT = int(os.environ.get("NAV_CHECKIN_TIMEOUT", str(DEFAULT_REQUEST_TIMEOUT)))

    sites = read_sites(sites_file)
    if max_sites > 0:
        sites = sites[:max_sites]
    if not sites:
        raise RuntimeError("站点列表为空")

    log.info("targets=%s proxy=%s timeout=%s", len(sites), proxy, REQUEST_TIMEOUT)

    linuxdo_cookie = os.environ.get("LINUXDO_COOKIE", "").strip()
    cookie_source = "env"
    if not linuxdo_cookie:
        linuxdo_cookie = bootstrap_linuxdo_cookie(proxy, username, password)
        cookie_source = "bootstrap(login+prewarm/shop_session)"
    if not linuxdo_cookie:
        raise RuntimeError("无法获取 LinuxDo 登录态：请先确保 shop/签到.py 能登录，或设置 LINUXDO_COOKIE/CDK_USERNAME/CDK_PASSWORD")

    log.info("linuxdo cookie source=%s", cookie_source)

    results = []
    for i, site in enumerate(sites, 1):
        r = run_one_site(site, proxy, username, password, linuxdo_cookie)
        results.append(r)

        icon = "✅" if r["success"] else "❌"
        ext = " (已签到)" if r.get("already") else ""
        err = f" | error={r['error']}" if r.get("error") else ""
        log.info("[%s/%s] %s %s%s%s", i, len(sites), icon, site, ext, err)
        delay(0.4, 0.9)

    success_sites = save_outputs(results, success_file, result_file)

    print("=" * 60)
    print(f"执行完成：total={len(results)} success={len(success_sites)}")
    print(f"成功列表：{success_file}")
    print(f"详细结果：{result_file}")


if __name__ == "__main__":
    main()