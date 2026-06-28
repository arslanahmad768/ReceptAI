import uuid
from fastapi import HTTPException
from app.database import get_db
from app.core.security import hash_password, verify_password

def register_user(email: str, password: str) -> dict:
    db = get_db()
    if db.table("profiles").select("id").eq("email", email).execute().data:
        raise HTTPException(status_code=409, detail="Email already registered")
    user_id = str(uuid.uuid4())
    db.table("profiles").insert({
        "id": user_id,
        "email": email,
        "password_hash": hash_password(password),
    }).execute()
    return {"id": user_id, "email": email}

def authenticate_user(email: str, password: str) -> dict:
    db = get_db()
    rows = db.table("profiles").select("id,email,password_hash").eq("email", email).execute()
    if not rows.data:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    user = rows.data[0]
    if not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"id": user["id"], "email": user["email"]}
