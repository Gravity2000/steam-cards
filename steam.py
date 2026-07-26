import cloudscraper
import time
import re
import urllib.parse
import random
import os
import threading
import logging
import json

logger = logging.getLogger("steam-tracker.steam")

PROXY = os.environ.get("PROXY_URL", "http://127.0.0.1:7890")

# 熔断状态持久化到文件，这样即使进程重启，也不会立刻又冲上去挨打
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "steam_rate_limit_state.json")

HEADERS = {
    "Accept": "application/json",
    "Referer": "https://steamcommunity.com/market/",
}

# 全局锁：确保任意时刻只有一路请求在跟 Steam 交互。
# 定时任务 / 手动刷新 / 添加卡牌 三个入口都会调用到这里，
# 如果不加锁，它们各自内部的 sleep(3~5s) 互相之间毫无约束，
# 短时间内会有多路请求并发打到 Steam，从而触发 429。
steam_lock = threading.Lock()

# ---------------- Session 管理 ----------------
# cloudscraper 的 session 需要先解一次 Cloudflare 的挑战才能拿到 cookie，
# 之后同一个 session 会一直复用这个 cookie。观察发现：如果一个 session
# 被拿去连续发起几十上百次 priceoverview 请求，很容易被 Steam 判定为
# 异常会话进而限流——即使单次孤立请求（比如 curl）完全没问题。
# 所以这里做两件事：
#   1) 每一轮"批量刷新"开始前，显式调用 start_new_session() 换一个新 session
#   2) 作为兜底，session 存活超过 SESSION_MAX_AGE 秒后也会自动换新
_session = None
_session_created_at = 0
SESSION_MAX_AGE = 1800  # 30分钟


def _new_session():
    global _session, _session_created_at
    _session = cloudscraper.create_scraper()
    _session.proxies = {"http": PROXY, "https": PROXY}
    _session_created_at = time.time()
    logger.info(f"[Steam] 创建新 session，代理: {PROXY}")


def get_session():
    """获取当前 session；超过最大存活时间则自动轮换"""
    global _session
    if _session is None or (time.time() - _session_created_at) > SESSION_MAX_AGE:
        _new_session()
    return _session


def start_new_session():
    """供每一轮批量刷新任务开始前显式调用，强制换一个新 session"""
    _new_session()


_new_session()  # 模块加载时先创建一个初始 session


# ---------------- 熔断机制 ----------------
# 如果连续几张卡牌的请求都是因为429彻底失败（重试3次仍然429），
# 说明当前这个IP/会话大概率已经被 Steam 限流封住了，与其死磕重试
# （只会让惩罚期越拖越长），不如直接熔断：接下来一段时间内所有
# 请求都直接跳过，等冷却期过了再恢复正常请求。
CIRCUIT_TRIP_THRESHOLD = 2          # 连续N张卡牌因429彻底失败，触发熔断
CIRCUIT_COOLDOWN_SECONDS = 30 * 60  # 熔断后冷却时间
_consecutive_429_failures = 0


def _load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        logger.warning(f"[Steam] 熔断状态写入失败: {e}")


def is_circuit_open():
    """当前是否处于熔断保护期，返回 (是否熔断, 剩余秒数)"""
    state = _load_state()
    blocked_until = state.get("blocked_until", 0)
    remaining = blocked_until - time.time()
    if remaining > 0:
        return True, int(remaining)
    return False, 0


def _trip_circuit():
    blocked_until = time.time() + CIRCUIT_COOLDOWN_SECONDS
    _save_state({"blocked_until": blocked_until})
    logger.error(
        f"[Steam] 连续 {CIRCUIT_TRIP_THRESHOLD} 张卡牌请求均被429限流，触发熔断保护，"
        f"接下来 {CIRCUIT_COOLDOWN_SECONDS // 60} 分钟内将跳过所有 Steam 请求"
    )
    # 顺便换个新session，万一冷却结束后旧session依然是"脏"的
    _new_session()


def _reset_circuit():
    _save_state({"blocked_until": 0})


# ---------------- 核心请求逻辑 ----------------
def _get_price(card_name: str):
    """只获取价格。返回结果里 rate_limited=True 表示本次失败是因为429耗尽重试，
    不是真的查无此商品——这两种失败原因不能用同一句"找不到该商品"来描述，
    否则日志会一直误导你。"""
    price_url = "https://steamcommunity.com/market/priceoverview/"
    price_params = {"appid": 753, "currency": 23, "market_hash_name": card_name}
    session = get_session()
    rate_limited = False
    for attempt in range(3):
        try:
            resp = session.get(price_url, params=price_params, timeout=15, headers=HEADERS)
            if resp.status_code == 429:
                rate_limited = True
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
                    "rate_limited": False,
                }
            # 请求成功返回，但 success != True —— 这才是真正的"查无此商品"
            rate_limited = False
        except Exception as e:
            logger.warning(f"价格请求第{attempt+1}次失败 [{card_name}]: {e}")
            if attempt < 2:
                time.sleep(2)
    if rate_limited:
        return {"success": False, "message": "请求被Steam限流(429)，本次未获取到价格", "rate_limited": True}
    return {"success": False, "message": "找不到该商品", "rate_limited": False}


def _get_image(card_name: str):
    """只获取图片 URL"""
    listing_url = f"https://steamcommunity.com/market/listings/753/{urllib.parse.quote(card_name)}/render"
    listing_params = {"start": 0, "currency": 23}
    session = get_session()
    try:
        resp = session.get(listing_url, params=listing_params, timeout=15, headers=HEADERS)
        match = re.search(r'https://community\.steamstatic\.com/economy/image/[^"\'\\\s]+', resp.text)
        if match:
            return match.group(0)
        match2 = re.search(r'icon_url\\":\\"([^\\]+)', resp.text)
        if match2:
            return f"https://community.steamstatic.com/economy/image/{match2.group(1)}"
    except Exception as e:
        logger.warning(f"图片请求失败 [{card_name}]: {e}")
    return None


def _record_result(result):
    """记录本次请求结果，用于判断是否需要触发/解除熔断"""
    global _consecutive_429_failures
    if result.get("rate_limited"):
        _consecutive_429_failures += 1
        if _consecutive_429_failures >= CIRCUIT_TRIP_THRESHOLD:
            _trip_circuit()
            _consecutive_429_failures = 0
    elif result.get("success"):
        _consecutive_429_failures = 0
        # 有成功请求，说明当前不处于限流状态，顺便解除可能残留的熔断标记
        _reset_circuit()


def get_card_info(card_name: str):
    """仅获取价格（刷新用，不请求图片）。全局串行化，避免并发触发429；
    处于熔断保护期时直接跳过，不发起真实请求。"""
    blocked, remaining = is_circuit_open()
    if blocked:
        logger.warning(f"[Steam] 熔断保护中，跳过查询: {card_name}（约{remaining // 60}分钟后恢复）")
        return {"success": False, "message": f"当前处于限流熔断保护中，约{remaining // 60}分钟后恢复", "rate_limited": True}

    with steam_lock:
        logger.info(f"[Steam] 查询价格开始: {card_name}")
        result = _get_price(card_name)
        logger.info(f"[Steam] 查询价格结束: {card_name} -> {result.get('lowest_price', result.get('message'))}")
        _record_result(result)
        time.sleep(random.uniform(5, 8))
    return result


def get_card_info_with_image(card_name: str):
    """获取价格 + 图片（添加卡牌时用）。全局串行化，避免并发触发429；
    处于熔断保护期时直接跳过，不发起真实请求。"""
    blocked, remaining = is_circuit_open()
    if blocked:
        logger.warning(f"[Steam] 熔断保护中，跳过查询: {card_name}（约{remaining // 60}分钟后恢复）")
        return {"success": False, "message": f"当前处于限流熔断保护中，约{remaining // 60}分钟后恢复", "rate_limited": True}

    with steam_lock:
        logger.info(f"[Steam] 查询价格+图片开始: {card_name}")
        result = _get_price(card_name)
        if result["success"]:
            result["image_url"] = _get_image(card_name)
        logger.info(f"[Steam] 查询价格+图片结束: {card_name} -> {result.get('lowest_price', result.get('message'))}")
        _record_result(result)
        time.sleep(random.uniform(5, 8))
    return result
