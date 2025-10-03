from fastapi import APIRouter,HTTPException
from pydantic import EmailStr
from models.users import User_Login, User_Signup
from config.database import users_collections
from auth.jwt_handler import create_token
router=APIRouter()

@router.post("/signup")
async def signup(user:User_Signup):
    existing = await users_collections.find_one({"useremail":user.useremail})
    if existing:
        raise HTTPException(status_code=400,detail="User Already exists")
    await users_collections.insert_one(user.dict())
    token=create_token({'useremail':existing["useremail"]})

    return{"msg":"New Account Created Successfully",'token':token}

@router.post("/login")
async def login(user:User_Login):
    existing = await users_collections.find_one({'useremail':user.useremail})
    if not existing:
        raise HTTPException(status_code=401,detail="User not registered")
    if user.userpassword!=existing["userpassword"]:
        raise HTTPException(status_code=401,detail="Password Incorrect")
    token=create_token({'useremail':existing["useremail"]})
    return{'msg':"Login Successful",'token':token}

@router.get("/login/{useremail}")
async def get_name(useremail:EmailStr):
    existing = await users_collections.find_one({'useremail':useremail})
    if not existing:
        HTTPException(status_code="404",detail="No user!!")
    return{'username':existing['username']}