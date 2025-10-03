from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import os

load_dotenv()

MONGO_URI=os.getenv("MONGOURI")
client=AsyncIOMotorClient(MONGO_URI)

db=client["AgenticAI"]
users_collections=db['users']