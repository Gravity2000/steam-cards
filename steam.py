import requests
import time

def get_card_info(card_name: str):
    # 获取价格
    price_url = "https://steamcommunity.com/market/priceoverview/"
    price_params = {
        "appid": 753,
        "currency": 23,
        "market_hash_name": card_name
    }

    # 获取图片
    listing_url = f"https://steamcommunity.com/market/listings/753/{card_name}/render"
    listing_params = {
        "start": 0,
        "currency": 23,
    }

    price_data = None
    for attempt in range(3):  # 最多重试3次
        try:
            price_res = requests.get(price_url, params=price_params, timeout=15)
            price_data = price_res.json()
            if price_data.get("success") == True:
                break
        except Exception as e:
            print(f"价格请求第{attempt+1}次失败: {e}")
            if attempt < 2:
                time.sleep(2)  # 等2秒再重试

    if not price_data or price_data.get("success") != True:
        return {"success": False, "message": "找不到该商品"}

    try:
        image_url = None
        listing_res = requests.get(listing_url, params=listing_params, timeout=15)
        listing_data = listing_res.json()

        assets = listing_data.get("assets", {}).get("753", {}).get("6", {})
        if assets:
            first_asset = list(assets.values())[0]
            icon = first_asset.get("icon_url")
            if icon:
                image_url = f"https://community.fastly.steamstatic.com/economy/image/{icon}"
    except Exception as e:
        print(f"图片请求失败: {e}")
        image_url = None

    lowest_price_str = price_data.get("lowest_price", "无数据")
    try:
        lowest_price_float = float(lowest_price_str.replace("¥ ", "").replace(",", ""))
    except:
        lowest_price_float = None

    
    time.sleep(3)  # 每张卡间隔3秒

    return {
        "success": True,
        "lowest_price": lowest_price_str,
        "lowest_price_float": lowest_price_float,
        "volume": price_data.get("volume", "无数据"),
        "median_price": price_data.get("median_price", "无数据"),
        "image_url": image_url
    }