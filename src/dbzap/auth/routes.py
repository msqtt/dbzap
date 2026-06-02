from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.exc import IntegrityError

from dbzap.auth.dependencies import make_get_current_user
from dbzap.auth.models import UserRecord
from dbzap.auth.passwords import hash_password, verify_password
from dbzap.auth.tokens import create_access_token
from dbzap.auth.user_store import UserStore
from dbzap.core.config import Settings


class RegisterRequest(BaseModel):
    username: str
    password: str

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class RegisterResponse(BaseModel):
    id: int
    username: str


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int


class MeResponse(BaseModel):
    id: int
    username: str


def create_auth_router(*, store: UserStore, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/auth")
    get_current_user = make_get_current_user(store=store, settings=settings)

    @router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
    async def register(body: RegisterRequest) -> RegisterResponse:
        try:
            user = await store.create_user(body.username, hash_password(body.password))
        except IntegrityError:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")
        return RegisterResponse(id=user.id, username=user.username)

    @router.post("/login", response_model=TokenResponse)
    async def login(body: LoginRequest) -> TokenResponse:
        _invalid = HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
        user = await store.get_by_username(body.username)
        if user is None or not verify_password(body.password, user.password_hash):
            raise _invalid
        token = create_access_token(
            {"sub": str(user.id)},
            secret=settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
            expire_minutes=settings.jwt_expire_minutes,
        )
        return TokenResponse(
            access_token=token,
            token_type="bearer",
            expires_in=settings.jwt_expire_minutes * 60,
        )

    @router.get("/me", response_model=MeResponse)
    async def me(user: UserRecord = Depends(get_current_user)) -> MeResponse:
        return MeResponse(id=user.id, username=user.username)

    return router
