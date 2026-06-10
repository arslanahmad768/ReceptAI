from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
from passlib.context import CryptContext
from app.config import get_settings

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return _pwd_ctx.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_ctx.verify(plain, hashed)

def create_access_token(data: dict) -> str:
    s = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=s.access_token_expire_minutes)
    return jwt.encode({**data, "exp": expire}, s.secret_key, algorithm="HS256")

def create_refresh_token(data: dict) -> str:
    s = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(days=s.refresh_token_expire_days)
    return jwt.encode(
        {**data, "exp": expire, "type": "refresh"},
        s.secret_key, algorithm="HS256"
    )

def verify_token(token: str) -> dict:
    try:
        return jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    except JWTError as exc:
        raise ValueError("Invalid token") from exc
