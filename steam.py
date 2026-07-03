import requests
import time
import re
import urllib.parse

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


def get_card_info(card_name: str):
    """仅获取价格（刷新用，不请求图片）"""
    result = _get_price(card_name)
    time.sleep(2)
    return result


def get_card_info_with_image(card_name: str):
    """获取价格 + 图片（添加卡牌时用）"""
    result = _get_price(card_name)
    if result["success"]:
        result["image_url"] = _get_image(card_name)
    time.sleep(2)
    return result