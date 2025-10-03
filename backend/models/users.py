from pydantic import BaseModel,EmailStr

class User_Signup(BaseModel):
    username:str
    useremail:EmailStr
    userpassword:str

class User_Login(BaseModel):
    useremail:EmailStr
    userpassword:str
