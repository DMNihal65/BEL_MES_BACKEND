from pydantic import BaseModel, EmailStr, constr, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

class UserBase(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=50)


class UserCreate(BaseModel):
    email: EmailStr
    username: str
    password: str
    role_id: int

    class Config:
        json_schema_extra = {
            "example": {
                "email": "user@example.com",
                "username": "testuser",
                "password": "password123",
                "role_id": 1
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
    user_id: int

class UserInDB(UserBase):
    id: int
    role: str
    access_list: str
    created_at: datetime
    is_active: bool

    class Config:
        from_attributes = True 

class UserRoleBase(BaseModel):
    role_name: str
    access_list: List[str]

class UserRoleCreate(UserRoleBase):
    class Config:
        json_schema_extra = {
            "example": {
                "role_name": "admin",
                "access_list": ["read", "write", "delete"]
            }
        }

class UserRoleUpdate(BaseModel):
    role_name: Optional[str] = None
    access_list: Optional[Dict[str, Any]] = None

class UserRoleResponse(UserRoleBase):
    id: int
    role_name: str
    access_list: List[str]

class UserRoleResponseNew(BaseModel):
    id: int
    role_name: str
    access_list: List[str]
    created_at: datetime

    class Config:
        from_attributes = True


class UserLogOut(BaseModel):
    id: int
    user_id: int
    login_timestamp: datetime
    logout_timestamp: Optional[datetime]

    class Config:
        orm_mode = True
