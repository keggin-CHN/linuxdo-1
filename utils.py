"""共享工具模块 - 环境变量、代理、TG推送、延迟、HTTP兼容层"""
import os
import random
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

# curl_cffi 可选，FreeBSD (serv00) 不支持，降级到 requests
try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    import requests as cffi_requests
    HAS_CURL_CFFI = False
    logging.getLogger("utils").info("curl_cffi 不可用，使用 requests 库")

IMPERSONATE_TARGETS = ["chrome133a", "chrome136", "chrome142"]


class CompatSession:
    """兼容 curl_cffi 和 requests 的 Session 包装器。
    统一 proxy 参数：外部始终传 proxy=str，内部自动适配。
    """

    def __init__(self, impersonate=None):
        if HAS_CURL_CFFI and impersonate:
            self._session = cffi_requests.Session(impersonate=impersonate)
        else:
            self._session = cffi_requests.Session()
        self._impersonate = impersonate

    @property
    def cookies(self):
        return self._session.cookies

    @cookies.setter
    def cookies(self, value):
        self._session.cookies = value

    def _adapt_kwargs(self, kwargs):
        """将 curl_cffi 风格的 proxy=str 转为 requests 风格的 proxies=dict"""
        if not HAS_CURL_CFFI:
            proxy = kwargs.pop("proxy", None)
            if proxy:
                kwargs["proxies"] = {"http": proxy, "https": proxy}
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
    """获取代理，优先环境变量，其次 Windows 注册表"""
    proxy = os.environ.get("CDK_PROXY", "")
    if proxy:
        return proxy
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
        enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
        if enable:
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
            winreg.CloseKey(key)
            if server and not server.startswith("http"):
                server = f"http://{server}"
            return server
        winreg.CloseKey(key)
    except Exception:
        pass
    return None


def delay(min_s=2, max_s=4):
    time.sleep(random.uniform(min_s, max_s))


def send_telegram(message, parse_mode=None):
    """发送 Telegram 消息"""
    token = os.environ.get("TG_BOT_TOKEN", "")
    chat_id = os.environ.get("TG_CHAT_ID", "")
    if not token or not chat_id:
        logging.getLogger("utils").warning("TG_BOT_TOKEN 或 TG_CHAT_ID 未设置，跳过推送")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    proxy = get_proxy()
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
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


def create_session(proxy=None):
    """创建兼容 session，curl_cffi 可用时带指纹模拟"""
    imp = random.choice(IMPERSONATE_TARGETS)
    session = CompatSession(impersonate=imp)
    return session, imp