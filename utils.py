"""共享工具模块 - 环境变量、代理、TG推送、延迟、HTTP兼容层"""
import os
import random
import time
import logging
import json as _json
import subprocess
import tempfile
import shutil
from http.cookiejar import MozillaCookieJar
from urllib.parse import urlencode, urlparse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

# curl_cffi 可选，FreeBSD (serv00) 不支持，降级到系统 curl
try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    cffi_requests = None
    HAS_CURL_CFFI = False
    logging.getLogger("utils").info("curl_cffi 不可用，使用系统 curl 命令")

IMPERSONATE_TARGETS = ["chrome133a", "chrome136", "chrome142"]

# ===== 写死默认配置（单用户）=====
HARDCODED_CDK_USERNAME = "zhou239289001@gmail.com"
HARDCODED_CDK_PASSWORD = "zhou060423rls"
HARDCODED_NODELOC_USERNAME = "zhou060423rls@gmail.com"
HARDCODED_NODELOC_PASSWORD = "Zhou060423rls"
HARDCODED_TG_BOT_TOKEN = "7483346980:AAHT4LBRiDU0H617sRQZmNUL8A6GumybMHE"
HARDCODED_TG_CHAT_ID = "7420206850"

# 检测系统 curl 是否可用
_SYSTEM_CURL = shutil.which("curl")
if not HAS_CURL_CFFI and not _SYSTEM_CURL:
    # 最后回退到 requests
    import requests as _fallback_requests
    logging.getLogger("utils").warning("系统 curl 也不可用，回退到 requests（可能被 CF 拦截）")


class CurlResponse:
    """模拟 requests.Response 的轻量对象"""
    def __init__(self, status_code=0, headers=None, body=b"", url=""):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body
        self.url = url

    @property
    def text(self):
        if isinstance(self._body, bytes):
            return self._body.decode("utf-8", errors="replace")
        return self._body

    def json(self):
        return _json.loads(self.text)


class CurlCookieJar:
    """简单的内存 cookie 管理，兼容 requests.cookies 接口"""
    def __init__(self):
        self._cookies = {}  # {(domain, name): value}
        self._simple = {}   # {name: value} 简单映射

    def set(self, name, value, domain=""):
        self._cookies[(domain, name)] = value
        self._simple[name] = value

    def get(self, name, default=None):
        return self._simple.get(name, default)

    def items(self):
        return self._simple.items()

    def update_from_header(self, set_cookie_header, request_url=""):
        """从 Set-Cookie header 解析并存储 cookie"""
        if not set_cookie_header:
            return
        # set_cookie_header 可能是单个或多个（curl -D 输出中每行一个）
        parts = set_cookie_header.split(",")
        for part in parts:
            part = part.strip()
            if "=" not in part:
                continue
            # 取第一个 ; 前的 name=value
            cookie_part = part.split(";")[0].strip()
            if "=" in cookie_part:
                name, _, value = cookie_part.partition("=")
                name = name.strip()
                value = value.strip()
                if name and not name.lower() in ("path", "domain", "expires", "max-age", "secure", "httponly", "samesite"):
                    domain = ""
                    if request_url:
                        try:
                            domain = urlparse(request_url).netloc
                        except Exception:
                            pass
                    self.set(name, value, domain)

    def to_cookie_string(self):
        """生成 Cookie: header 值"""
        if not self._simple:
            return ""
        return "; ".join(f"{k}={v}" for k, v in self._simple.items())

    def __iter__(self):
        return iter(self._simple)

    def __len__(self):
        return len(self._simple)


class CurlSubprocessSession:
    """使用系统 curl 命令的 Session，TLS 指纹正常不会被 CF 拦截"""

    def __init__(self, impersonate=None):
        self._cookies = CurlCookieJar()
        self._impersonate = impersonate
        # 创建临时 cookie 文件
        self._cookie_file = tempfile.mktemp(suffix=".txt", prefix="curl_cookies_")

    @property
    def cookies(self):
        return self._cookies

    @cookies.setter
    def cookies(self, value):
        if isinstance(value, CurlCookieJar):
            self._cookies = value
        else:
            # 从其他类型的 cookie jar 复制
            new_jar = CurlCookieJar()
            if hasattr(value, 'items'):
                for name, val in value.items():
                    new_jar.set(name, val)
            self._cookies = new_jar

    def _build_curl_cmd(self, method, url, headers=None, data=None, json_data=None,
                        proxy=None, timeout=30, allow_redirects=True, params=None):
        """构建 curl 命令行"""
        if params:
            sep = "&" if "?" in url else "?"
            url = url + sep + urlencode(params)

        cmd = [_SYSTEM_CURL, "-s", "-S"]  # silent but show errors
        # 输出 response headers
        cmd += ["-D", "-"]  # dump headers to stdout
        # method
        if method.upper() == "POST":
            cmd += ["-X", "POST"]
        elif method.upper() == "PUT":
            cmd += ["-X", "PUT"]
        elif method.upper() == "DELETE":
            cmd += ["-X", "DELETE"]
        # timeout
        if timeout:
            cmd += ["--max-time", str(timeout)]
            cmd += ["--connect-timeout", str(min(timeout, 15))]
        # redirects
        if not allow_redirects:
            pass  # curl 默认不跟随重定向
        else:
            cmd += ["-L", "--max-redirs", "15"]
        # proxy
        if proxy:
            cmd += ["--proxy", proxy]
        # cookies
        cookie_str = self._cookies.to_cookie_string()
        if cookie_str:
            cmd += ["-H", f"Cookie: {cookie_str}"]
        # headers
        if headers:
            for k, v in headers.items():
                cmd += ["-H", f"{k}: {v}"]
        # 默认 headers
        cmd += ["-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"]
        cmd += ["-H", "Accept: */*"]
        # data
        if json_data is not None:
            cmd += ["-H", "Content-Type: application/json"]
            cmd += ["-d", _json.dumps(json_data)]
        elif data is not None:
            if isinstance(data, dict):
                cmd += ["-d", urlencode(data)]
                cmd += ["-H", "Content-Type: application/x-www-form-urlencoded"]
            else:
                cmd += ["-d", str(data)]
        # URL
        cmd.append(url)
        return cmd

    def _parse_response(self, stdout, url):
        """解析 curl -D - 的输出：headers + body"""
        # curl -D - 输出格式：
        # HTTP/1.1 200 OK\r\n
        # Header: value\r\n
        # \r\n
        # body...
        # 如果有重定向且 -L，会有多组 headers
        resp = CurlResponse(url=url)

        if isinstance(stdout, bytes):
            raw = stdout
        else:
            raw = stdout.encode("utf-8", errors="replace")

        # 找最后一组 HTTP headers（处理重定向情况）
        # 分割 headers 和 body：找最后一个 \r\n\r\n
        parts = raw.split(b"\r\n\r\n")
        if len(parts) < 2:
            # 没有标准分隔，尝试 \n\n
            parts = raw.split(b"\n\n")

        if len(parts) >= 2:
            # 找最后一组 headers
            # headers 部分可能包含多组（重定向），取最后一组
            body = parts[-1]
            header_sections = parts[:-1]

            # 合并所有 header sections，找最后一个 HTTP/ 开头的
            all_headers_raw = b"\r\n\r\n".join(header_sections)
            # 找所有 HTTP 响应的起始位置
            import re as _re
            http_starts = [m.start() for m in _re.finditer(b"HTTP/", all_headers_raw)]

            if http_starts:
                last_header_block = all_headers_raw[http_starts[-1]:]
                header_lines = last_header_block.decode("utf-8", errors="replace").split("\n")

                # 解析状态行
                status_line = header_lines[0].strip()
                # HTTP/1.1 200 OK or HTTP/2 200
                status_parts = status_line.split(None, 2)
                if len(status_parts) >= 2:
                    try:
                        resp.status_code = int(status_parts[1])
                    except ValueError:
                        resp.status_code = 0

                # 解析 headers
                for hl in header_lines[1:]:
                    hl = hl.strip()
                    if ":" in hl:
                        hname, _, hval = hl.partition(":")
                        hname = hname.strip().lower()
                        hval = hval.strip()
                        resp.headers[hname] = hval
                        # 解析 cookies
                        if hname == "set-cookie":
                            self._cookies.update_from_header(hval, url)

                # 处理 location header（保留原始大小写）
                for hl in header_lines[1:]:
                    hl = hl.strip()
                    if hl.lower().startswith("location:"):
                        resp.headers["location"] = hl.split(":", 1)[1].strip()

            resp._body = body
            # 更新最终 URL（如果有 location 且 allow_redirects）
            if "location" in resp.headers and resp.status_code not in (301, 302, 303, 307, 308):
                resp.url = resp.headers.get("location", url)
        else:
            resp._body = raw
            resp.status_code = 0

        return resp

    def _request(self, method, url, **kwargs):
        headers = kwargs.get("headers", {})
        data = kwargs.get("data")
        json_data = kwargs.get("json")
        proxy = kwargs.get("proxy")
        timeout = kwargs.get("timeout", 30)
        allow_redirects = kwargs.get("allow_redirects", True)
        params = kwargs.get("params")

        cmd = self._build_curl_cmd(
            method, url, headers=headers, data=data, json_data=json_data,
            proxy=proxy, timeout=timeout, allow_redirects=allow_redirects,
            params=params,
        )

        try:
            result = subprocess.run(
                cmd, capture_output=True, timeout=timeout + 10,
            )
            resp = self._parse_response(result.stdout, url)
            if resp.status_code == 0 and result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="replace")
                logging.getLogger("curl").warning(f"curl 错误 (exit={result.returncode}): {stderr[:200]}")
            return resp
        except subprocess.TimeoutExpired:
            logging.getLogger("curl").warning(f"curl 超时: {url}")
            return CurlResponse(status_code=0, url=url)
        except Exception as e:
            logging.getLogger("curl").warning(f"curl 异常: {e}")
            return CurlResponse(status_code=0, url=url)

    def get(self, url, **kwargs):
        return self._request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._request("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self._request("PUT", url, **kwargs)

    def delete(self, url, **kwargs):
        return self._request("DELETE", url, **kwargs)

    def __del__(self):
        try:
            if os.path.exists(self._cookie_file):
                os.unlink(self._cookie_file)
        except Exception:
            pass


class CompatSession:
    """兼容 curl_cffi 和系统 curl 的 Session 包装器。
    优先级: curl_cffi > 系统 curl > requests
    统一 proxy 参数：外部始终传 proxy=str，内部自动适配。
    """

    def __init__(self, impersonate=None):
        if HAS_CURL_CFFI and impersonate:
            self._session = cffi_requests.Session(impersonate=impersonate)
            self._backend = "curl_cffi"
        elif HAS_CURL_CFFI:
            self._session = cffi_requests.Session()
            self._backend = "curl_cffi"
        elif _SYSTEM_CURL:
            self._session = CurlSubprocessSession(impersonate=impersonate)
            self._backend = "system_curl"
        else:
            self._session = _fallback_requests.Session()
            self._backend = "requests"
        self._impersonate = impersonate

    @property
    def cookies(self):
        return self._session.cookies

    @cookies.setter
    def cookies(self, value):
        self._session.cookies = value

    def _adapt_kwargs(self, kwargs):
        """适配不同后端的参数差异"""
        if self._backend == "requests":
            proxy = kwargs.pop("proxy", None)
            if proxy:
                kwargs["proxies"] = {"http": proxy, "https": proxy}
        # system_curl 和 curl_cffi 都支持 proxy=str
        return kwargs

    def get(self, url, **kwargs):
        return self._session.get(url, **self._adapt_kwargs(kwargs))

    def post(self, url, **kwargs):
        return self._session.post(url, **self._adapt_kwargs(kwargs))

    def put(self, url, **kwargs):
        return self._session.put(url, **self._adapt_kwargs(kwargs))

    def delete(self, url, **kwargs):
        return self._session.delete(url, **self._adapt_kwargs(kwargs))


def get_proxy():
    """默认直连：统一不使用代理"""
    return None


def delay(min_s=2, max_s=4):
    time.sleep(random.uniform(min_s, max_s))


def send_telegram(message, parse_mode=None, proxy=None):
    """发送 Telegram 消息。proxy 可显式传入，也可通过 TG_PROXY 环境变量配置。"""
    token = os.environ.get("TG_BOT_TOKEN", "") or HARDCODED_TG_BOT_TOKEN
    chat_id = os.environ.get("TG_CHAT_ID", "") or HARDCODED_TG_CHAT_ID
    if not token or not chat_id:
        logging.getLogger("utils").warning("TG_BOT_TOKEN 或 TG_CHAT_ID 未设置，跳过推送")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    if proxy is None:
        proxy = os.environ.get("TG_PROXY") or get_proxy()
    try:
        payload = {"chat_id": chat_id, "text": message}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        s = CompatSession()
        s.post(url, json=payload, proxy=proxy, timeout=15)
    except Exception as e:
        logging.getLogger("utils").warning(f"Telegram 推送异常: {e}")


def load_env(env_path=None):
    """加载 .env 文件（本地开发用，GitHub Actions 不需要）"""
    if env_path is None:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())

    # 注入写死默认值（.env 中已配置则优先用 .env）
    if not os.environ.get("CDK_USERNAME"):
        os.environ["CDK_USERNAME"] = HARDCODED_CDK_USERNAME
    if not os.environ.get("CDK_PASSWORD"):
        os.environ["CDK_PASSWORD"] = HARDCODED_CDK_PASSWORD
    if not os.environ.get("NODELOC_USERNAME"):
        os.environ["NODELOC_USERNAME"] = HARDCODED_NODELOC_USERNAME
    if not os.environ.get("NODELOC_PASSWORD"):
        os.environ["NODELOC_PASSWORD"] = HARDCODED_NODELOC_PASSWORD
    if not os.environ.get("TG_BOT_TOKEN"):
        os.environ["TG_BOT_TOKEN"] = HARDCODED_TG_BOT_TOKEN
    if not os.environ.get("TG_CHAT_ID"):
        os.environ["TG_CHAT_ID"] = HARDCODED_TG_CHAT_ID


def create_session(proxy=None):
    """创建兼容 session，curl_cffi 可用时带指纹模拟"""
    imp = random.choice(IMPERSONATE_TARGETS)
    session = CompatSession(impersonate=imp)
    return session, imp