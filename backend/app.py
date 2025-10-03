from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes.users import router as UserRouter

app=FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # React frontend
    allow_credentials=True,
    allow_methods=["*"],  # GET, POST, PUT, DELETE, etc.
    allow_headers=["*"],  # Authorization, Content-Type, etc.
)

app.include_router(UserRouter)