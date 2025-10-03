from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import Request, HTTPException
from auth.jwt_handler import decode_token

class JWTBearer(HTTPBearer):
    async def __call__(self, request: Request):
        credentials: HTTPAuthorizationCredentials = await super().__call__(request)
        if credentials:
            try:
                payload = decode_token(credentials.credentials)
                return payload["email"]
            except:
                raise HTTPException(status_code=403, detail="Invalid token")
        raise HTTPException(status_code=403, detail="Token missing")
