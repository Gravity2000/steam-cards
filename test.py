import json
import requests

res=requests.get("https://steamcommunity.com/market/listings/753/2600700-HAPPY%20HALLOWEEN%20%28Foil%29/render/?currency=23&start=0")
data=res.json()
print(json.dumps(data, indent=4, ensure_ascii=False))