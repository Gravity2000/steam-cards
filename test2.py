import requests


headers = {
    "Referer": "https://www.douban.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
res = requests.get("https://img3.doubanio.com/view/subject/m/public/s35385193.jpg", headers=headers)

print("状态码：", res.status_code)
print("内容类型：", res.headers.get("Content-Type"))
print("内容大小：", len(res.content), "字节")