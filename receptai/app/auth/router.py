from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from starlette.requests import Request
from app.auth.models import UserIn, UserOut, TokenResponse, RefreshRequest
from app.auth.service import register_user, authenticate_user
from app.core.security import create_access_token, create_refresh_token, verify_token
from app.core.rate_limit import limiter

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/register", status_code=201, response_model=UserOut)
@limiter.limit("5/minute")
async def register(request: Request, body: UserIn):
    return register_user(body.email, body.password)

@router.post("/token", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(request: Request, form: OAuth2PasswordRequestForm = Depends()):
    user = authenticate_user(form.username, form.password)
    return TokenResponse(
        access_token=create_access_token({"sub": user["id"]}),
        refresh_token=create_refresh_token({"sub": user["id"]}),
    )

@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest):
    payload = verify_token(body.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Not a refresh token")
    return TokenResponse(
        access_token=create_access_token({"sub": payload["sub"]}),
        refresh_token=create_refresh_token({"sub": payload["sub"]}),
    )
