import cloudscraper
import time
import re
import urllib.parse
import random
import os
import threading
import logging

logger = logging.getLogger("steam-tracker.steam")

PROXY = os.environ.get("PROXY_URL", "http://127.0.0.1:7890")

_session = cloudscraper.create_scraper()
_session.proxies = {"http": PROXY, "https": PROXY}
logger.info(f"[Steam] 代理: {PROXY}")

HEADERS = {
    "Accept": "application/json",
    "Referer": "https://steamcommunity.com/market/",
}

# 全局锁：确保任意时刻只有一路请求在跟 Steam 交互。
# 定时任务 / 手动刷新 / 添加卡牌 三个入口都会调用到这里，
# 如果不加锁，它们各自内部的 sleep(3~5s) 互相之间毫无约束，
# 短时间内会有多路请求并发打到 Steam，从而触发 429。
steam_lock = threading.Lock()


def _get_price(card_name: str):
    """只获取价格"""
    price_url = "https://steamcommunity.com/market/priceoverview/"
    price_params = {"appid": 753, "currency": 23, "market_hash_name": card_name}
    for attempt in range(3):
        try:
            resp = _session.get(price_url, params=price_params, timeout=15, headers=HEADERS)
            if resp.status_code == 429:
                wait = 20 * (attempt + 1)
                logger.warning(f"Steam 限流(429)，等待 {wait} 秒后重试... [{card_name}]")
                time.sleep(wait)
                continue
            data = resp.json()
            if data and data.get("success") == True:
                lowest_price_str = data.get("lowest_price", "无数据")
                try:
                    lowest_price_float = float(lowest_price_str.replace("¥ ", "").replace(",", ""))
                except Exception:
                    lowest_price_float = None
                return {
                    "success": True,
                    "lowest_price": lowest_price_str,
                    "lowest_price_float": lowest_price_float,
                    "volume": data.get("volume", "无数据"),
                    "median_price": data.get("median_price", "无数据"),
                }
        except Exception as e:
            logger.warning(f"价格请求第{attempt+1}次失败 [{card_name}]: {e}")
            if attempt < 2:
                time.sleep(2)
    return {"success": False, "message": "找不到该商品"}


def _get_image(card_name: str):
    """只获取图片 URL"""
    listing_url = f"https://steamcommunity.com/market/listings/753/{urllib.parse.quote(card_name)}/render"
    listing_params = {"start": 0, "currency": 23}
    try:
        resp = _session.get(listing_url, params=listing_params, timeout=15, headers=HEADERS)
        match = re.search(r'https://community\.steamstatic\.com/economy/image/[^"\'\\\s]+', resp.text)
        if match:
            return match.group(0)
        match2 = re.search(r'icon_url\\":\\"([^\\]+)', resp.text)
        if match2:
            return f"https://community.steamstatic.com/economy/image/{match2.group(1)}"
    except Exception as e:
        logger.warning(f"图片请求失败 [{card_name}]: {e}")
    return None


def get_card_info(card_name: str):
    """仅获取价格（刷新用，不请求图片）。全局串行化，避免并发触发429。"""
    with steam_lock:
        logger.info(f"[Steam] 查询价格开始: {card_name}")
        result = _get_price(card_name)
        logger.info(f"[Steam] 查询价格结束: {card_name} -> {result.get('lowest_price', result.get('message'))}")
        time.sleep(random.uniform(5, 8))
    return result


def get_card_info_with_image(card_name: str):
    """获取价格 + 图片（添加卡牌时用）。全局串行化，避免并发触发429。"""
    with steam_lock:
        logger.info(f"[Steam] 查询价格+图片开始: {card_name}")
        result = _get_price(card_name)
        if result["success"]:
            result["image_url"] = _get_image(card_name)
        logger.info(f"[Steam] 查询价格+图片结束: {card_name} -> {result.get('lowest_price', result.get('message'))}")
        time.sleep(random.uniform(5, 8))
    return result