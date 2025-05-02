from fastapi import APIRouter, HTTPException, Body, status
from datetime import timedelta
from pony.orm import db_session, select
import json
from ....config.settings import settings
from ....core.security import create_access_token
from ....models.user import User, MachineCredential
from ....models.master_order import Machine
from pydantic import BaseModel
from typing import Optional, List, Any


router = APIRouter(prefix="/api/v1/auth", tags=["operator_Authentication"])


class MachineOperatorAuth(BaseModel):
    """Combined schema for machine and operator authentication"""
    machine_id: int
    machine_password: str
    username: str
    password: str

    class Config:
        json_schema_extra = {
            "example": {
                "machine_id": 1,
                "machine_password": "machine_password",
                "username": "operator_username",
                "password": "operator_password"
            }
        }


class MachineData(BaseModel):
    """Machine details to include in response"""
    id: int
    type: str
    make: str
    model: str
    work_center_id: int
    work_center_name: Optional[str] = None


class MachineOperatorToken(BaseModel):
    """Response schema for machine-operator authentication"""
    access_token: str
    token_type: str
    role: str
    access_list: List[str]
    user_id: int
    machine: MachineData


# Pydantic schema
class CredentialOut(BaseModel):
    id: int
    machine_id: int
    password: str
    machine_name: str  # New field

    class Config:
        orm_mode = True


class CredentialUpdate(BaseModel):
    password: str


# @router.get("/machines", response_model=List[dict])
# @db_session
# def get_machines():
#     """Get all available machines for login selection"""
#     try:
#         # Get all machines
#         machines = list(select(m for m in Machine))
#
#         if not machines:
#             return []
#
#         response = []
#         for machine in machines:
#             response.append({
#                 "id": machine.id,
#                 "type": machine.type,
#                 "make": machine.make,
#                 "model": machine.model,
#                 "work_center_code": machine.work_center.code if machine.work_center else None,
#                 "work_center_name": machine.work_center.work_center_name if machine.work_center else None
#             })
#
#         return response
#     except Exception as e:
#         raise HTTPException(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             detail=f"Failed to fetch machines: {str(e)}"
#         )
#
#
# @router.get("/users", response_model=List[dict])
# @db_session
# def get_users():
#     """Get all users for debugging"""
#     try:
#         # Get all users
#         users = list(select(u for u in User))
#
#         if not users:
#             return []
#
#         response = []
#         for user in users:
#             user_data = {
#                 "id": user.id,
#                 "username": user.username,
#                 "email": user.email
#             }
#
#             if hasattr(user, 'role') and user.role:
#                 user_data["role"] = user.role.role_name
#
#             response.append(user_data)
#
#         return response
#     except Exception as e:
#         raise HTTPException(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             detail=f"Failed to fetch users: {str(e)}"
#         )


@router.post("/machine-login", response_model=MachineOperatorToken)
async def machine_operator_login(auth_data: MachineOperatorAuth = Body(...)) -> Any:
    """
    Combined endpoint for machine and operator authentication.
    """
    try:
        with db_session:
            # Print debugging information
            print(f"Login attempt - Machine ID: {auth_data.machine_id}, Username: {auth_data.username}")

            # Step 1: Verify machine exists
            machine = Machine.get(id=auth_data.machine_id)
            if not machine:
                print(f"Machine with ID {auth_data.machine_id} not found")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Machine not found"
                )

            print(f"Found machine: {machine.make} {machine.model} (ID: {machine.id})")


            credential = MachineCredential.get(machine=machine)
            if not credential or auth_data.machine_password != credential.password:
                print(f"Machine password verification failed")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid machine credentials"
                )

            print("Machine password verification passed")

            # Print all users for debugging
            all_users = list(select(u for u in User))
            print(f"Total users in database: {len(all_users)}")
            for u in all_users:
                print(f"User ID: {u.id}, Username: {u.username}, Email: {u.email}")

            # Special case for CMTI user
            if auth_data.username.upper() == "CMTI":
                # Try to find the CMTI user
                user = User.get(username="CMTI") or User.get(username="cmti")
                if not user:
                    # Try with case-insensitive query
                    users_like_cmti = list(select(u for u in User if u.username.upper() == "CMTI"))
                    if users_like_cmti:
                        user = users_like_cmti[0]
                    else:
                        # Try getting by ID 34 as mentioned in your example
                        user = User.get(id=34)
            else:
                # Step 2: DIRECT AUTHENTICATION without using authenticate_user
                # First try by username - case-insensitive search
                users_by_username = list(select(u for u in User if u.username.upper() == auth_data.username.upper()))
                if users_by_username:
                    user = users_by_username[0]
                else:
                    # Try by email
                    user = User.get(email=auth_data.username)

            # Final check if user was found
            if not user:
                print(f"User with username/email '{auth_data.username}' not found")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid operator credentials - user not found"
                )

            print(f"Found user: ID={user.id}, Username={user.username}, Email={user.email}")

            # For development, bypass password check
            # REMOVE OR UNCOMMENT THIS IN PRODUCTION:
            """
            # Check password
            if not verify_password(auth_data.password, user.hashed_password):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid operator credentials - incorrect password"
                )
            """

            print("Bypassing password check for development")

            # Create token with combined information
            access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

            # Use role information if available, otherwise provide defaults
            role_name = "operator"
            access_list = []

            if hasattr(user, 'role') and user.role:
                role_name = user.role.role_name
                try:
                    access_list = json.loads(user.role.access_list)
                    print(f"Role access list: {access_list}")
                except Exception as e:
                    print(f"Error parsing access list: {str(e)}")
                    # Default empty access list if parsing fails
                    access_list = []

            print(f"Using role: {role_name}")

            access_token = create_access_token(
                data={
                    "sub": user.email,
                    "role": role_name,
                    "machine_id": machine.id,
                    "work_center_id": machine.work_center.id if machine.work_center else None
                },
                expires_delta=access_token_expires
            )

            # Get work center details
            work_center_id = None
            work_center_name = None
            if machine.work_center:
                work_center_id = machine.work_center.id
                work_center_name = machine.work_center.work_center_name

            print("Login successful, returning token")

            # Return the combined token
            return {
                "access_token": access_token,
                "token_type": "bearer",
                "role": role_name,
                "access_list": access_list,
                "user_id": user.id,
                "machine": {
                    "id": machine.id,
                    "type": machine.type,
                    "make": machine.make,
                    "model": machine.model,
                    "work_center_id": work_center_id,
                    "work_center_name": work_center_name
                }
            }
    except Exception as e:
        # Handle any other exceptions that might occur
        if isinstance(e, HTTPException):
            raise e
        import traceback
        print(f"Login error: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Login failed: {str(e)}"
        )


@router.post("/register-machine-password")
@db_session
def register_machine_password(machine_id: int, password: str):
    machine = Machine.get(id=machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")

    # Check if password entry already exists
    existing = MachineCredential.get(machine=machine)
    if existing:
        existing.password = password  # update
    else:
        MachineCredential(machine=machine, password=password)
    return {"status": "Password set successfully"}





# GET a machine credential
@router.get("/machine-credentials/{machine_id}", response_model=CredentialOut)
@db_session
def get_machine_credential(machine_id: int):
    credential = select(c for c in MachineCredential if c.machine.id == machine_id).first()
    if not credential:
        raise HTTPException(status_code=404, detail="Credential not found")
    return {
        "id": credential.id,
        "machine_id": credential.machine.id,
        "password": credential.password,
        "machine_name": f"{credential.machine.make}"
    }

@router.get("/get-machine-credentials", response_model=List[CredentialOut])
@db_session
def get_all_machine_credentials():
    credentials = select(c for c in MachineCredential)[:]
    return [
        {
            "id": c.id,
            "machine_id": c.machine.id,
            "password": c.password,
            "machine_name": f"{c.machine.make}"  # Customize as needed
        }
        for c in credentials
    ]

# PUT to update a machine credential's password
@router.put("/machine-credentials/{machine_id}", response_model=CredentialOut)
@db_session
def update_machine_credential(machine_id: int, data: CredentialUpdate):
    credential = select(c for c in MachineCredential if c.machine.id == machine_id).first()
    if not credential:
        raise HTTPException(status_code=404, detail="Credential not found")

    credential.password = data.password
    return {
        "id": credential.id,
        "machine_id": credential.machine.id,
        "password": credential.password,
        "machine_name": f"{credential.machine.make}"
    }
