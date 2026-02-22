"""NodeLoc (nodeloc.com) - 自动签到"""
import os
import sys
import json
import time
import random
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import create_session, load_env, send_telegram

BASE_URL = "https://www.nodeloc.com"

# ===== 写死配置（按你的要求）=====
HARDCODED_NODELOC_USERNAME = "zhou060423rls@gmail.com"
HARDCODED_NODELOC_PASSWORD = "Zhou060423rls"
HARDCODED_TG_BOT_TOKEN = "7483346980:AAHT4LBRiDU0H617sRQZmNUL8A6GumybMHE"
HARDCODED_TG_CHAT_ID = "7420206850"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("NodeLoc")

def generate_nonce():
    part1 = ''.join(random.choices('0123456789abcdefghijklmnopqrstuvwxyz', k=13))
    part2 = ''.join(random.choices('0123456789abcdefghijklmnopqrstuvwxyz', k=13))
    return part1 + part2

class NodeLocClient:
    def __init__(self):
        self.session, self.impersonate = create_session()
        log.info(f"浏览器指纹: {self.impersonate}")
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": BASE_URL,
            "Referer": BASE_URL + "/"
        }
        self.csrf_token = ""

    def get_csrf_token(self):
        log.info(f"[*] Getting CSRF Token from {BASE_URL}/session/csrf ...")
        try:
            response = self.session.get(f"{BASE_URL}/session/csrf", headers=self.headers)
            csrf_data = response.json()
            self.csrf_token = csrf_data.get("csrf")
            log.info(f"[+] CSRF Token: {self.csrf_token}")
            return True
        except Exception as e:
            log.error(f"[-] Failed to get CSRF Token: {e}")
            return False

    def login(self, username, password):
        log.info(f"[*] Logging in as {username} ...")
        login_headers = self.headers.copy()
        login_headers["X-CSRF-Token"] = self.csrf_token
        login_headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        
        data = {
            "login": username,
            "password": password
        }
        
        try:
            login_response = self.session.post(f"{BASE_URL}/session", headers=login_headers, data=data)
                
            if login_response.status_code == 200:
                login_data = login_response.json()
                if "error" in login_data:
                    log.error(f"[-] Login failed: {login_data['error']}")
                    return False, login_data['error']
                log.info("[+] Login successful!")
                return True, "Login successful"
            else:
                log.error(f"[-] Login failed with status code: {login_response.status_code}")
                return False, f"Status code: {login_response.status_code}"
        except Exception as e:
            log.error(f"[-] Login error: {e}")
            return False, str(e)

    def set_cookie(self, cookie_str):
        log.info("[*] Using provided Cookie ...")
        for cookie_pair in cookie_str.split(';'):
            if '=' in cookie_pair:
                key, value = cookie_pair.strip().split('=', 1)
                self.session.cookies.set(key, value)

    def checkin(self):
        log.info(f"[*] Attempting to checkin ...")
        
        # Refresh CSRF token
        if not self.get_csrf_token():
            return False, "Failed to refresh CSRF token"

        nonce = generate_nonce()
        timestamp = int(time.time() * 1000)

        checkin_headers = self.headers.copy()
        checkin_headers["X-CSRF-Token"] = self.csrf_token
        checkin_headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        checkin_headers["X-Discourse-Checkin"] = "true"
        checkin_headers["X-Checkin-Nonce"] = nonce

        checkin_data = {
            "nonce": nonce,
            "timestamp": timestamp
        }

        checkin_url = f"{BASE_URL}/checkin"
        log.info(f"[*] Sending checkin request to {checkin_url} ...")
        
        try:
            checkin_response = self.session.post(checkin_url, headers=checkin_headers, data=checkin_data)
            
            if checkin_response.status_code == 200:
                try:
                    resp_json = checkin_response.json()
                    if resp_json.get("success"):
                        msg = resp_json.get('message', 'OK')
                        log.info(f"[+] Checkin SUCCESS! Message: {msg}")
                        return True, f"签到成功: {msg}"
                    elif "已经签到" in str(resp_json.get("message", "")) or "already" in str(resp_json.get("message", "")).lower():
                        msg = resp_json.get('message')
                        log.info(f"[+] Already checked in today. Message: {msg}")
                        return True, f"今日已签到: {msg}"
                    else:
                        log.error(f"[-] Checkin failed. Response: {resp_json}")
                        return False, f"签到失败: {resp_json}"
                except json.JSONDecodeError:
                    log.error(f"[-] Response is not JSON. Content: {checkin_response.text[:100]}")
                    return False, "响应非JSON格式"
            else:
                log.error(f"[-] Request failed with status {checkin_response.status_code}")
                return False, f"请求失败: {checkin_response.status_code}"

        except Exception as e:
            log.error(f"[-] Checkin error: {e}")
            return False, f"签到异常: {e}"

def run():
    # 账号密码按要求写死；仍保留 NODELOC_COOKIE 作为可选优先登录方式
    username = HARDCODED_NODELOC_USERNAME
    password = HARDCODED_NODELOC_PASSWORD
    cookie = os.environ.get("NODELOC_COOKIE", "").strip()

    client = NodeLocClient()

    # Step 1: Get CSRF Token
    if not client.get_csrf_token():
        return False, "NodeLoc 签到\n❌ 获取 CSRF Token 失败"

    # Step 2: Login
    if cookie:
        client.set_cookie(cookie)
    else:
        success, msg = client.login(username, password)
        if not success:
            return False, f"NodeLoc 签到\n❌ 登录失败: {msg}"

    # Step 3: Checkin
    success, msg = client.checkin()
    
    status_icon = "✅" if success else "❌"
    return success, f"NodeLoc 签到\n{status_icon} {msg}"

def main():
    load_env()
    # TG 按要求写死到脚本（通过环境变量注入给 utils.send_telegram）
    os.environ["TG_BOT_TOKEN"] = HARDCODED_TG_BOT_TOKEN
    os.environ["TG_CHAT_ID"] = HARDCODED_TG_CHAT_ID

    success, msg = run()
    log.info(msg)
    send_telegram(msg)

if __name__ == "__main__":
    main()