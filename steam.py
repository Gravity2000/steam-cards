import requests

def get_card_price(card_name:str):
    url="https://steamcommunity.com/market/priceoverview"
    params={
        "appid":753,
        "currency":23,
        "market_hash_name":card_name
    }
    try:
        response=requests.get(url,params=params,timeout=10)
        data=response.json()
        if data.get("success"):
            return {
                "success":True,
                "lowest_price":data.get("lowest_price","无数据"),
                "volume":data.get("volume","无数据"),
                "median_price":data.get("median_price","无数据")
            }
        return {"success":False,"message":"找不到该商品"}
    except Exception as e:
        return {"success":False,"message":str(e)}