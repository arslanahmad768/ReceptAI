from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
import bcrypt
from app.config import get_settings

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())

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
