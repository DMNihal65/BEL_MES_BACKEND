from fastapi import APIRouter, HTTPException, Depends, Body, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from datetime import timedelta, datetime
from typing import Any, List
from ....config.settings import settings
from ....core.security import create_access_token
from ....models import UserLogs
from ....schemas.user import UserCreate, Token, UserLogin, UserResponse, UserRoleCreate, UserRoleResponse, \
    UserRoleUpdate, UserRoleResponseNew, UserLogOut
from ....crud.user import create_user, authenticate_user, get_user_by_email
from ....models.user import UserRole, User
from pony.orm import db_session, select, commit, flush, desc
import json
from pytz import timezone

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")
ist = timezone("Asia/Kolkata")


@router.post("/register", response_model=UserResponse)
async def register_user(user_data: UserCreate = Body(...)) -> Any:
    try:
        with db_session:
            # Check if email exists
            if User.get(email=user_data.email):
                raise HTTPException(
                    status_code=400,
                    detail="Email already registered"
                )

            # Get the role
            role = UserRole.get(id=user_data.role_id)
            if not role:
                raise HTTPException(
                    status_code=404,
                    detail="Role not found"
                )

            # Create user with role object
            user = create_user(
                email=user_data.email,
                username=user_data.username,
                password=user_data.password,
                role=role
            )

            return {
                "message": "User registered successfully",
                "email": user.email,
                "username": user.username,
                "role": user.role.role_name
            }
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )


@router.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()) -> Any:
    try:
        with db_session:
            user = authenticate_user(username=form_data.username, password=form_data.password)
            if not user:
                raise HTTPException(
                    status_code=401,
                    detail="Incorrect username or password"
                )

            # Get current UTC time and convert to IST
            ist = timezone("Asia/Kolkata")
            utc_now = datetime.utcnow()
            login_time = utc_now.replace(tzinfo=timezone("UTC")).astimezone(ist)

            UserLogs(
                user=user,
                login_timestamp=login_time
            )

            access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
            access_token = create_access_token(
                data={
                    "sub": user.email,
                    "role": user.role.role_name
                },
                expires_delta=access_token_expires
            )

            return {
                "access_token": access_token,
                "token_type": "bearer",
                "user_id": user.id,
                "role": user.role.role_name,
                "access_list": json.loads(user.role.access_list)
            }

    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )


@router.get("/roles", response_model=List[UserRoleResponse])
@db_session
def get_roles():
    """Get all available roles and their access permissions"""
    try:
        # Use explicit schema reference
        roles = list(UserRole.select())  # Convert to list immediately

        if not roles:
            return []

        response = []
        for role in roles:
            try:
                response.append({
                    "id": role.id,
                    "role_name": role.role_name,
                    "access_list": json.loads(role.access_list) if role.access_list else []
                })
            except (json.JSONDecodeError, AttributeError):
                # Handle any JSON parsing errors
                response.append({
                    "id": role.id,
                    "role_name": role.role_name,
                    "access_list": []
                })

        return response

    except Exception as e:
        import traceback
        print(f"Error details: {traceback.format_exc()}")  # Add debug logging
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to fetch roles: {str(e)}"
        )


@router.post("/roles", response_model=UserRoleResponse)
@db_session
def create_role(role: UserRoleCreate):
    """Create a new role with specified permissions."""
    try:
        # Check if the role name already exists
        if UserRole.get(role_name=role.role_name):
            raise HTTPException(
                status_code=400,
                detail="Role name already exists"
            )

        # Convert the access list to JSON string
        access_list_json = json.dumps(role.access_list)

        # Create the new role
        new_role = UserRole(
            role_name=role.role_name,
            access_list=access_list_json
        )

        # Commit changes to assign an ID to the new role
        commit()

        # Log the details of the new role
        # print("+++++++++++++++++++++++++++++++++++++++")
        # print(f"ID: {new_role.id}")
        # print(f"Role Name: {new_role.role_name}")
        # print(f"Access List: {new_role.access_list}")
        # print("+++++++++++++++++++++++++++++++++++++++")

        return {
            "id": new_role.id,
            "role_name": new_role.role_name,
            "access_list": json.loads(new_role.access_list)
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Role creation failed: {str(e)}"
        )


@router.put("/roles/{role_id}", response_model=UserRoleResponse)
@db_session
def update_role(role_id: int, role_update: UserRoleUpdate):
    """Update an existing role's permissions"""
    role = UserRole.get(id=role_id)
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found"
        )

    if role_update.role_name is not None:
        role.role_name = role_update.role_name
    if role_update.access_list is not None:
        role.access_list = json.dumps(role_update.access_list)

    return {
        "id": role.id,
        "role_name": role.role_name,
        "access_list": json.loads(role.access_list)
    }


@router.put("/users/{user_id}/role")
@db_session
def update_user_role(user_id: int, role_id: int):
    """Update a user's role"""
    user = User.get(id=user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    role = UserRole.get(id=role_id)
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found"
        )

    user.role = role
    return {"message": "User role updated successfully"}


@router.get("/users/{username}/role", response_model=UserRoleResponseNew)
@db_session
def get_user_role(username: str):
    """Get the role of a specific user by their username"""
    try:
        user = User.get(username=username)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if not user.is_active:
            raise HTTPException(status_code=404, detail="User is not active")

        # Convert the access_list string to a list
        try:
            access_list = json.loads(user.role.access_list)
        except json.JSONDecodeError:
            # Fallback if the string is comma-separated
            access_list = [x.strip() for x in user.role.access_list.split(',')]

        return UserRoleResponseNew(
            id=user.id,  # Changed from user.role.id to user.id
            role_name=user.role.role_name,
            access_list=access_list,
            created_at=user.role.created_at
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/login-logs", response_model=List[UserLogOut])
def get_login_logs():
    with db_session:
        logs = select(log for log in UserLogs)[:]

        result = []
        for log in logs:
            login_ts = log.login_timestamp
            logout_ts = log.logout_timestamp

            # Ensure timezone-aware conversion
            if login_ts and login_ts.tzinfo is None:
                login_ts = login_ts.replace(tzinfo=timezone("UTC"))
            if logout_ts and logout_ts.tzinfo is None:
                logout_ts = logout_ts.replace(tzinfo=timezone("UTC"))

            result.append(UserLogOut(
                id=log.id,
                user_id=log.user.id,
                login_timestamp=login_ts.astimezone(ist),
                logout_timestamp=logout_ts.astimezone(ist) if logout_ts else None
            ))

        return result


@router.get("/api/v1/auth/users-get")
@db_session
def get_users( active_only: bool = True):
    users = select(u for u in User if (not active_only) or u.is_active)

    result = []
    for user in users:
        result.append({
            "id": user.id,
            "username": user.username,
            "role": {
                "role_name": user.role.role_name,
                "access_list": user.role.access_list,
                "created_at": user.role.created_at.isoformat() if user.role.created_at else None,
            },
            "created_at": user.created_at.isoformat() if user.created_at else None,
            # add other fields as needed
        })
    return result

@router.delete("/users/{user_id}", status_code=204)
@db_session
def delete_user(user_id: int):
    user = User.get(id=user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.delete()
    return