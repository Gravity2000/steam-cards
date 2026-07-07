import requests
import time
import re
import urllib.parse
import random
import threading

def _get_price(card_name: str):
    """只获取价格"""
    price_url = "https://steamcommunity.com/market/priceoverview/"
    price_params = {
        "appid": 753,
        "currency": 23,
        "market_hash_name": card_name
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://steamcommunity.com/market/",
    }
    for attempt in range(3):
        try:
            resp = requests.get(price_url, params=price_params, timeout=15, headers=headers)
            if resp.status_code == 429:
                wait = 10 * (attempt + 1)
                print(f"Steam 限流(429)，等待 {wait} 秒后重试...")
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
        except Exception as e:
            print(f"价格请求第{attempt+1}次失败: {e}")
            if attempt < 2:
                time.sleep(2)

    # 判断是否因为限流导致失败
    if 'resp' in locals() and resp.status_code == 429:
        return {"success": False, "message": "Steam 限流"}
    return {"success": False, "message": "找不到该商品"}


def _get_image(card_name: str):
    """只获取图片 URL"""
    listing_url = f"https://steamcommunity.com/market/listings/753/{urllib.parse.quote(card_name)}/render"
    listing_params = {"start": 0, "currency": 23}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://steamcommunity.com/market/",
    }
    try:
        resp = requests.get(listing_url, params=listing_params, timeout=15, headers=headers)
        match = re.search(r'https://community\.steamstatic\.com/economy/image/[^"\'\\\s]+', resp.text)
        if match:
            return match.group(0)
        match2 = re.search(r'icon_url\\":\\"([^\\]+)', resp.text)
        if match2:
            return f"https://community.steamstatic.com/economy/image/{match2.group(1)}"
    except Exception as e:
        print(f"图片请求失败: {e}")
    return None


# 全局请求节流：记录上次请求时间
_last_request_time = 0
_lock = threading.Lock()

# 限流冷却机制
_rate_limited_until = 0      # 冷却截止时间戳
_cooldown_duration = 300      # 每次触发冷却持续 300 秒（5分钟）
_cooldown_streak = 0          # 连续限流次数，用于指数递增冷却

def _check_cooldown():
    """检查是否处于冷却期，如果是则等待冷却结束"""
    global _rate_limited_until
    now = time.time()
    if now < _rate_limited_until:
        wait = _rate_limited_until - now
        print(f"⏳ 处于冷却期，等待 {wait:.0f} 秒后继续...")
        time.sleep(wait)

def _trigger_cooldown():
    """触发冷却，记录冷却截止时间（指数递增）"""
    global _rate_limited_until, _cooldown_streak, _cooldown_duration
    with _lock:
        _cooldown_streak += 1
        duration = _cooldown_duration * _cooldown_streak  # 连续限流则翻倍：5min, 10min, 15min...
        _rate_limited_until = time.time() + duration
        print(f"🚫 触发冷却！暂停 {duration} 秒（第 {_cooldown_streak} 次限流）")

def _clear_cooldown():
    """一次成功的请求后，逐渐降低冷却等级"""
    global _cooldown_streak
    if _cooldown_streak > 0:
        with _lock:
            _cooldown_streak = max(0, _cooldown_streak - 1)

def _throttle(min_interval: float = 3.0):
    """确保两次请求之间至少间隔 min_interval 秒"""
    global _last_request_time
    _check_cooldown()
    with _lock:
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        _last_request_time = time.time()


def get_card_info(card_name: str):
    """仅获取价格（刷新用，不请求图片）"""
    _throttle(3.0)
    result = _get_price(card_name)

    if not result.get("success") and "限流" in result.get("message", ""):
        _trigger_cooldown()
    elif result.get("success"):
        _clear_cooldown()

    delay = random.uniform(5, 10)
    time.sleep(delay)
    return result


def get_card_info_with_image(card_name: str):
    """获取价格 + 图片（添加卡牌时用）"""
    _throttle(3.0)
    result = _get_price(card_name)
    if result["success"]:
        result["image_url"] = _get_image(card_name)

    if not result.get("success") and "限流" in result.get("message", ""):
        _trigger_cooldown()
    elif result.get("success"):
        _clear_cooldown()

    delay = random.uniform(5, 10)
    time.sleep(delay)
    return result