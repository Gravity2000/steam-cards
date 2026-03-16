from fastapi import FastAPI,Depends,HTTPException
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordBearer,OAuth2PasswordRequestForm
from sqlalchemy import create_engine,Column,Integer,String,Float
from sqlalchemy.orm import declarative_base,sessionmaker,Session
from apscheduler.schedulers.background import BackgroundScheduler
from steam import get_card_price
from auth import hash_password,verify_password,create_token,decode_token
import smtplib
from email.mime.text import MIMEText
from urllib.parse import unquote
import resend
from dotenv import load_dotenv
import os
load_dotenv(override=False)

app=FastAPI()

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
        
        print(f"邮件发送成功:{email}")
    except Exception as e:
        print(f"邮件发送失败,{e}")

def refresh_all_prices():
    db=SessionLocal()
    cards=db.query(Card).all()
    for card in cards:
        result=get_card_price(card.name)
        if result["success"]:
            card.last_price=result["lowest_price"]
            db.commit()
            if card.alert_price:
                price_value=float(result["lowest_price"].replace("¥ ","").replace(",",""))
                if price_value<=card.alert_price:
                    user=db.query(User).filter(User.username==card.owner).first()
                    if user and user.email:
                        send_email(user.email,card.name,result["lowest_price"],card.alert_price)
    db.close()

#定时任务
schedular=BackgroundScheduler()
schedular.add_job(refresh_all_prices,"interval",minutes=30)
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
    result=get_card_price(name)
    if not result["success"]:
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
def refresh_prices(db:Session=Depends(get_db),username:str=Depends(get_current_user)):
    cards=db.query(Card).filter(Card.owner==username).all()
    for card in cards:
        result=get_card_price(card.name)
        if result["success"]:
            card.last_price=result["lowest_price"]
            db.commit()
    return {"success":True,"message":"价格刷新成功"}

@app.put("/user/email")
def update_email(email:str,db:Session=Depends(get_db),username:str=Depends(get_current_user)):
    user=db.query(User).filter(User.username==username).first()
    if user:
        user.email=email
        db.commit()
        return {"success":True,"message":"邮箱更新成功"}
    return {"success":False,"message":"用户不存在"}