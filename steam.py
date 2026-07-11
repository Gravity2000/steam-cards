import requests
import time
import re
import urllib.parse
import random
import threading
import os

# ============================================================
# 全局 Session（复用连接 + Cookie，模拟真实浏览器）
# 走 Clash 代理避免 Steam 限流
# ============================================================
PROXY = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or "http://127.0.0.1:7890"
_session = requests.Session()
_session.proxies = {"http": PROXY, "https": PROXY}
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://steamcommunity.com/market/",
    "Origin": "https://steamcommunity.com",
})

_session_initialized = False
_session_lock = threading.Lock()

def _init_session():
    """初始化 Session：访问 Steam 市场获取 Cookie"""
    global _session_initialized
    if _session_initialized:
        return
    with _session_lock:
        if _session_initialized:
            return
        try:
            resp = _session.get("https://steamcommunity.com/market/", timeout=15)
            print(f"[Session] Steam Session 已初始化")
            _session_initialized = True
        except Exception as e:
            print(f"[Session] 初始化失败: {e}")

def _refresh_session():
    """定期刷新 Session Cookie（每 30 分钟调用一次）"""
    global _session_initialized
    try:
        _session.get("https://steamcommunity.com/market/", timeout=15)
        print("[Session] Cookie 已刷新")
    except Exception as e:
        print(f"[Session] 刷新失败: {e}")
        _session_initialized = False

# ============================================================
# 价格获取
# ============================================================
def _get_price(card_name: str):
    """只获取价格（使用持久 Session）"""
    _init_session()
    price_url = "https://steamcommunity.com/market/priceoverview/"
    price_params = {
        "appid": 753,
        "currency": 23,
        "market_hash_name": card_name
    }
    for attempt in range(3):
        try:
            resp = _session.get(price_url, params=price_params, timeout=15)
            if resp.status_code == 429:
                wait = 10 * (attempt + 1)
                print(f"Steam 限流(429)，等待 {wait} 秒后重试...")
                _refresh_session()
                time.sleep(wait)
                continue
            data = resp.json()
            if data and data.get("success") == True:
                lowest_price_str = data.get("lowest_price", "无数据")
                try:
                    lowest_price_float = float(lowest_price_str.replace("¥ ", "").replace(",", ""))
                except:
                    lowest_price_float = None
                return {
                    "success": True,
                    "lowest_price": lowest_price_str,
                    "lowest_price_float": lowest_price_float,
                    "volume": data.get("volume", "无数据"),
                    "median_price": data.get("median_price", "无数据"),
                }
        except requests.exceptions.ConnectionError:
            print(f"连接错误，刷新 Session 重试...")
            _session_initialized = False
            _init_session()
            time.sleep(3)
        except Exception as e:
            print(f"价格请求第{attempt+1}次失败: {e}")
            if attempt < 2:
                time.sleep(2)
    return {"success": False, "message": "找不到该商品"}


def _get_image(card_name: str):
    """只获取图片 URL"""
    _init_session()
    listing_url = f"https://steamcommunity.com/market/listings/753/{urllib.parse.quote(card_name)}/render"
    listing_params = {"start": 0, "currency": 23}
    try:
        resp = _session.get(listing_url, params=listing_params, timeout=15)
        match = re.search(r'https://community\.steamstatic\.com/economy/image/[^"\'\\\s]+', resp.text)
        if match:
            return match.group(0)
        match2 = re.search(r'icon_url\\":\\"([^\\]+)', resp.text)
        if match2:
            return f"https://community.steamstatic.com/economy/image/{match2.group(1)}"
    except Exception as e:
        print(f"图片请求失败: {e}")
    return None


# ============================================================
# 节流 + 冷却机制
# ============================================================
_last_request_time = 0
_lock = threading.Lock()
_rate_limited_until = 0
_cooldown_streak = 0

_REFRESH_INTERVAL = 1800  # 每 30 分钟刷新一次 Cookie
_last_refresh_time = 0

def _check_cooldown():
    global _rate_limited_until
    now = time.time()
    if now < _rate_limited_until:
        wait = _rate_limited_until - now
        print(f"[冷却] 等待 {wait:.0f} 秒...")
        time.sleep(wait)

def _throttle():
    """请求节流：两次请求之间至少间隔 3 秒"""
    global _last_request_time, _last_refresh_time
    _check_cooldown()
    now = time.time()
    # 每 30 分钟刷新一次 Session
    if now - _last_refresh_time > _REFRESH_INTERVAL:
        _refresh_session()
        _last_refresh_time = now
    with _lock:
        elapsed = now - _last_request_time
        if elapsed < 3.0:
            time.sleep(3.0 - elapsed)
        _last_request_time = time.time()


def get_card_info(card_name: str):
    """仅获取价格（刷新用，不请求图片）"""
    _throttle()
    result = _get_price(card_name)
    delay = random.uniform(5, 10)
    time.sleep(delay)
    return result


def get_card_info_with_image(card_name: str):
    """获取价格 + 图片（添加卡牌时用）"""
    _throttle()
    result = _get_price(card_name)
    if result["success"]:
        result["image_url"] = _get_image(card_name)
    delay = random.uniform(5, 10)
    time.sleep(delay)
    return result