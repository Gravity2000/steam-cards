import requests

def get_card_info(card_name:str):
    #获取价格
    price_url="https://steamcommunity.com/market/priceoverview/"
    price_params={
        "appid":753,
        "currency":23,
        "market_hash_name":card_name
    }

    #获取图片
    listing_url = f"https://steamcommunity.com/market/listings/753/{card_name}/render"
    listing_params={
        "start": 0,
        #"count": 1,
        "currency": 23,
        #"language": "english",
        #"format": "json"
    }
    try:
        price_res=requests.get(price_url,params=price_params,timeout=10)
        price_data=price_res.json()

        image_url=None
        listing_res=requests.get(listing_url,params=listing_params,timeout=10)
        listing_data=listing_res.json()

        #从assets里提取icon_url
        assets=listing_data.get("assets",{}).get("753",{}).get("6",{})
        if assets:
            first_asset=list(assets.values())[0]
            icon=first_asset.get("icon_url")
            if icon:
                image_url=f"https://community.fastly.steamstatic.com/economy/image/{icon}"

        if price_data.get("success")==True:
            return {
                "success":True,
                "lowest_price":price_data.get("lowest_price","无数据"),
                "volume":price_data.get("volume","无数据"),
                "median_price":price_data.get("median_price","无数据"),
                "image_url":image_url
            }
        return {"success":False,"message":"找不到该商品"}
    except Exception as e:
        return {"success":False,"message":str(e)}
    
def get_card_price(card_name:str):
    result=get_card_info(card_name)
    return result