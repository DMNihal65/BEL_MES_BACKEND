from fastapi import APIRouter, HTTPException, Depends, Body
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from datetime import timedelta
from typing import Any
from ....core.config import settings
from ....core.security import create_access_token
from ....schemas.user import UserCreate, Token, UserLogin, UserResponse
from ....crud.user import create_user, authenticate_user, get_user_by_email
import json

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/login")

@router.post("/register", response_model=UserResponse)
async def register_user(user_data: UserCreate = Body(...)) -> Any:
    try:
        if get_user_by_email(user_data.email):
            raise HTTPException(
                status_code=400,
                detail="Email already registered"
            )
        
        user = create_user(
            email=user_data.email,
            username=user_data.username,
            password=user_data.password,
            role=user_data.role,
            access_list=user_data.access_list
        )
        
        return {
            "message": "User registered successfully",
            "email": user.email,
            "username": user.username,
            "role": user.role
        }
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )

@router.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()) -> Any:
    try:
        # form_data.username will contain the email
        user = authenticate_user(username=form_data.username, password=form_data.password)
        if not user:
            raise HTTPException(
                status_code=401,
                detail="Incorrect email or password"
            )
        
        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": user.username, "role": user.role},
            expires_delta=access_token_expires
        )
        
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "role": user.role,
            "access_list": json.loads(user.access_list)
        }
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=str(e)
        ) 