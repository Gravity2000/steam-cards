from fastapi import FastAPI,Depends,HTTPException
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordBearer,OAuth2PasswordRequestForm
from sqlalchemy import create_engine,Column,Integer,String,Float
from sqlalchemy.orm import declarative_base,sessionmaker,Session
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
import os
load_dotenv(override=False)

import logging
# 关键：用 logging 替代 print，并显式配置。
# print() 在日志被重定向到文件/被 systemd、docker 等托管时，默认是
# "块缓冲"而不是"行缓冲"，导致日志不会实时输出，攒够缓冲区或进程退出
# 时才会一次性刷出来，看起来就像"点了刷新很久没反应"。
# format 里加了 threadName，方便看出是不是有多个线程在同时刷新（并发是429限流的根因之一）。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)s] %(message)s",
)
logger = logging.getLogger("steam-tracker")

from steam import get_card_info, get_card_info_with_image
from auth import hash_password,verify_password,create_token,decode_token
from urllib.parse import unquote
import resend
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import threading

app=FastAPI()

# 防抖标记：避免用户连点"刷新"或定时任务与手动刷新撞车时，
# 同一时间起多个刷新流程互相并发请求 Steam。
is_refreshing = False
refresh_state_lock = threading.Lock()

#数据库配置
DATABASE_URL=os.getenv("DATABASE_URL","sqlite:///cards.db")
engine=create_engine(DATABASE_URL)
Base=declarative_base()
SessionLocal=sessionmaker(bind=engine)
oauth2_scheme=OAuth2PasswordBearer(tokenUrl="login")

#用户表
class User(Base):
    __tablename__="users"
    id=Column(Integer,primary_key=True)
    username=Column(String,unique=True)
    password=Column(String)
    email=Column(String)

#卡牌表
class Card(Base):
    __tablename__="cards"
    id=Column(Integer,primary_key=True)
    name=Column(String)
    alert_price=Column(Float,nullable=True)
    last_price=Column(String,nullable=True)
    owner=Column(String)
    image_url=Column(String,nullable=True)

Base.metadata.create_all(engine)

def get_db():
    db=SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(token:str=Depends(oauth2_scheme)):
    username=decode_token(token)
    if username is None:
        raise HTTPException(status_code=401,detail="请先登录")
    return username

def send_email(to_email:str,card_name:str,price:str,alert_price:float):
    resend.api_key=os.getenv("RESEND_API_KEY")
    try:
        email = resend.Emails.send({
            "from": "onboarding@resend.dev",
            "to": [to_email],
            "subject": "Steam 价格提醒：" + card_name,
            "text": "你关注的卡牌 " + card_name + " 当前价格为 " + price + "，已低于你设定的 ¥" + str(alert_price) + "！"
        })
        
        logger.info(f"邮件发送成功:{email}")
    except Exception as e:
        logger.error(f"邮件发送失败,{e}")

def refresh_all_prices():
    global is_refreshing
    with refresh_state_lock:
        if is_refreshing:
            logger.info("[定时任务] 已有刷新任务在进行中，本次跳过")
            return
        is_refreshing = True

    db=SessionLocal()
    try:
        cards=db.query(Card).all()

        unique_name=list(set(card.name for card in cards))
        price_cache={}

        logger.info(f"[定时任务] 开始刷新 {len(unique_name)} 种卡牌价格...")
        for name in unique_name:
            price_cache[name]=get_card_info(name)
            logger.info(f"[定时任务] 查询 [{name}] -> {price_cache[name].get('lowest_price', '失败')}")

        for card in cards:
            result=price_cache.get(card.name)
            if result and result["success"]:
                # Steam 返回"无数据"时不覆盖旧价格
                if result.get("lowest_price") and result["lowest_price"] != "无数据":
                    card.last_price=result["lowest_price"]
                    db.commit()
                    logger.info(f"[定时任务] 更新 [{card.name}] 价格: {result['lowest_price']}")
                if card.alert_price:
                    price_value=result.get("lowest_price_float")
                    logger.info(f"[定时任务] [{card.name}] 当前价格:{price_value} 期望价格:{card.alert_price}")
                    if  price_value and price_value<=card.alert_price:
                        user=db.query(User).filter(User.username==card.owner).first()
                        if user and user.email:
                            send_email(user.email,card.name,result["lowest_price"],card.alert_price)
        logger.info("[定时任务] 刷新完成")
    finally:
        db.close()
        with refresh_state_lock:
            is_refreshing = False

#定时任务 - 每60分钟刷新一次，减少 Steam 限流风险
schedular=BackgroundScheduler()
schedular.add_job(refresh_all_prices,"interval",minutes=60)
schedular.start()

@app.get("/")
def index():
    return FileResponse("index.html")

@app.post("/register")
def register(form_data: OAuth2PasswordRequestForm = Depends(), email: str = "", db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.username == form_data.username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="用户名已存在")
    user = User(username=form_data.username, password=hash_password(form_data.password), email=email)
    db.add(user)
    db.commit()
    return {"message": "注册成功"}


@app.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_token({"sub": user.username})
    return {"access_token": token, "token_type": "bearer"}

@app.get("/cards")
def get_cards(db:Session=Depends(get_db),username:str=Depends(get_current_user)):
    cards=db.query(Card).filter(Card.owner==username).all()
    return{"cards":[{"id":c.id,"name":c.name,"alert_price":c.alert_price,"last_price":c.last_price,"image_url":c.image_url}for c in cards]}

@app.post("/cards")
def add_card(name :str,db:Session=Depends(get_db),username:str=Depends(get_current_user)):
    name=unquote(name)
    logger.info(f"[添加卡牌] 用户 {username} 添加: {name}")
    result=get_card_info_with_image(name)
    if not result["success"]:
        logger.warning(f"[添加卡牌] 查询失败: {name}")
        return {"success":False,"message":"找不到该商品，请检查商品名"}
    card=Card(name=name,last_price=result["lowest_price"],owner=username,image_url=result.get("image_url"))
    db.add(card)
    db.commit()
    return{"success":True,"message":"添加成功","price":result["lowest_price"]}

@app.put("/cards/{id}/alert")
def set_alert(id:int,alert_price:float,db:Session=Depends(get_db),username=Depends(get_current_user)):
    card=db.query(Card).filter(Card.id==id,Card.owner==username).first()
    if card:
        card.alert_price=alert_price
        db.commit()
        return{"success":True,"message":"提醒价格设置成功"}
    return {"success":False,"message":"找不到该卡牌"}

@app.delete("/cards/{id}")
def delete_card(id:int,db:Session=Depends(get_db),username:str=Depends(get_current_user)):
    card=db.query(Card).filter(Card.id==id,Card.owner==username).first()
    if card:
        db.delete(card)
        db.commit()
        return{"success":True,"message":"删除成功"}
    return{"success":False,"message":"找不到该卡牌"}

@app.post("/refresh")
def refresh_prices(username: str = Depends(get_current_user)):
    """异步后台刷新，立即返回。
    加了防抖：如果已经有刷新任务在跑（无论是定时任务还是别的用户点的手动刷新），
    直接返回提示，不再新开一路请求，避免并发打 Steam 导致429，
    也避免多路刷新的日志互相交织让人误以为"卡住了"。
    """
    global is_refreshing
    with refresh_state_lock:
        if is_refreshing:
            logger.info(f"[手动刷新] 用户 {username} 请求被忽略：已有刷新任务在进行中")
            return {"success": False, "message": "已有刷新任务在进行中，请稍后再试"}
        is_refreshing = True

    def _run():
        global is_refreshing
        db = SessionLocal()
        try:
            cards = db.query(Card).filter(Card.owner == username).all()
            unique_names = list(set(card.name for card in cards))
            logger.info(f"[手动刷新] 用户 {username} 开始刷新 {len(unique_names)} 种卡牌...")
            for name in unique_names:
                result = get_card_info(name)
                if result["success"]:
                    for card in cards:
                        if card.name == name:
                            if result.get("lowest_price") and result["lowest_price"] != "无数据":
                                card.last_price = result["lowest_price"]
                                db.commit()
                            if card.alert_price:
                                price_value = result.get("lowest_price_float")
                                logger.info(f"卡牌:{card.name} 当前价格:{price_value} 期望价格:{card.alert_price}")
                                if price_value and price_value <= card.alert_price:
                                    user = db.query(User).filter(User.username == username).first()
                                    if user and user.email:
                                        send_email(user.email, card.name, result["lowest_price"], card.alert_price)
                else:
                    logger.warning(f"卡牌 [{name}] 刷新失败: {result.get('message')}")
            logger.info(f"[手动刷新] 用户 {username} 刷新完成")
        except Exception as e:
            logger.error(f"刷新出错: {e}")
        finally:
            db.close()
            with refresh_state_lock:
                is_refreshing = False
    threading.Thread(target=_run, daemon=True).start()
    return {"success": True, "message": "刷新已启动"}

@app.put("/user/email")
def update_email(email:str,db:Session=Depends(get_db),username:str=Depends(get_current_user)):
    user=db.query(User).filter(User.username==username).first()
    if user:
        user.email=email
        db.commit()
        return {"success":True,"message":"邮箱更新成功"}
    return {"success":False,"message":"用户不存在"}