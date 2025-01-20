from typing import Optional
from pony.orm import db_session
from ..models.user import User
from ..core.security import get_password_hash, verify_password
import json

@db_session
def create_user(email: str, username: str, password: str, role) -> Optional[User]:
    try:
        hashed_password = get_password_hash(password)
        user = User(
            email=email,
            username=username,
            hashed_password=hashed_password,
            role=role
        )
        return user
    except Exception as e:
        raise Exception(f"Error creating user: {str(e)}")

@db_session
def authenticate_user(username: str, password: str) -> Optional[User]:
    """Authenticate user by username/email and password"""
    # Try to find user by username or email
    user = User.get(username=username) or User.get(email=username)
    
    if not user:
        return None
    
    if not verify_password(password, user.hashed_password):
        return None
    
    return user

@db_session
def get_user_by_email(email: str) -> Optional[User]:
    return User.get(email=email) 