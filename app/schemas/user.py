from pydantic import BaseModel, EmailStr, constr
from typing import Optional, List
from datetime import datetime

class UserBase(BaseModel):
    email: EmailStr
    username: constr(min_length=3, max_length=50)

class UserCreate(UserBase):
    password: constr(min_length=6)
    role: str = "user"
    access_list: List[str] = []

    class Config:
        json_schema_extra = {
            "example": {
                "email": "user@example.com",
                "username": "testuser",
                "password": "password123",
                "role": "user",
                "access_list": ["read", "write"]
            }
        }

class UserLogin(BaseModel):
    email: EmailStr
    password: str

    class Config:
        json_schema_extra = {
            "example": {
                "email": "user@example.com",
                "password": "password123"
            }
        }

class UserResponse(BaseModel):
    message: str
    email: str
    username: str
    role: str

class Token(BaseModel):
    access_token: str
    token_type: str
    role: str
    access_list: List[str]

class UserInDB(UserBase):
    id: int
    role: str
    access_list: str
    created_at: datetime
    is_active: bool

    class Config:
        from_attributes = True 