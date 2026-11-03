"""Auth routes: signup, signin, guest login, OTP, refresh, signout, and /api/me."""
import asyncio

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from typing import Optional

from app.core.config import SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY
from app.auth.dependencies import get_current_user

router = APIRouter(tags=["auth"])


class SignUpRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None

class SignInRequest(BaseModel):
    email: EmailStr
    password: str

class AuthResponse(BaseModel):
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    user: Optional[dict] = None
    message: Optional[str] = None

class RefreshRequest(BaseModel):
    refresh_token: str

@router.post("/auth/signup", response_model=AuthResponse)
async def sign_up(body: SignUpRequest):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/auth/v1/signup",
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": body.email, "password": body.password, "data": {"full_name": body.full_name}},
        )
    data = resp.json()
    if resp.status_code != 200 or data.get("error"):
        msg = data.get("error_description") or data.get("msg") or "Signup failed"
        raise HTTPException(status_code=400, detail=msg)
    # Supabase returns 200 with no access_token for duplicate emails (user enumeration protection)
    if "access_token" not in data:
        # Check if user already exists by attempting sign-in - if it succeeds, email is taken
        async with httpx.AsyncClient() as client:
            check = await client.post(
                f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
                headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
                json={"email": body.email, "password": body.password},
            )
        if check.status_code == 200:
            raise HTTPException(status_code=400, detail="An account with this email already exists. Please sign in instead.")
        return AuthResponse(message="confirmation_required")
    return AuthResponse(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        user={
            "id": data["user"]["id"],
            "email": data["user"]["email"],
            "full_name": data["user"].get("user_metadata", {}).get("full_name"),
        },
    )

class GuestRequest(BaseModel):
    name: str
    email: EmailStr

@router.post("/auth/guest", response_model=AuthResponse)
async def guest_login(body: GuestRequest):
    import secrets
    from app.core.config import SUPABASE_SERVICE_ROLE_KEY

    # Generate a random password - guest never needs to know it
    random_password = secrets.token_urlsafe(24)

    # Use admin API to create user with email auto-confirmed (no OTP needed)
    async with httpx.AsyncClient() as client:
        create = await client.post(
            f"{SUPABASE_URL}/auth/v1/admin/users",
            headers={
                "apikey": SUPABASE_SERVICE_ROLE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "email": body.email,
                "password": random_password,
                "email_confirm": True,
                "user_metadata": {"full_name": body.name, "is_guest": True},
            },
        )

    created = create.json()

    # If email already exists, just sign them in directly
    if create.status_code != 200:
        msg = created.get("msg") or created.get("error") or ""
        if "already" not in msg.lower() and "exists" not in msg.lower():
            raise HTTPException(status_code=400, detail="Could not create guest account. Try a different email.")

    # Sign in to get tokens (works whether newly created or already exists)
    async with httpx.AsyncClient() as client:
        signin = await client.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": body.email, "password": random_password},
        )

    signin_data = signin.json()
    if signin.status_code != 200 or not signin_data.get("access_token"):
        # Email exists with a different password - account already registered
        raise HTTPException(status_code=400, detail="This email is already registered. Please sign in instead.")

    return AuthResponse(
        access_token=signin_data["access_token"],
        refresh_token=signin_data["refresh_token"],
        user={
            "id": signin_data["user"]["id"],
            "email": signin_data["user"]["email"],
            "full_name": body.name,
        },
    )

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class VerifyOtpRequest(BaseModel):
    email: EmailStr
    token: str
    password: str

class VerifySignupOtpRequest(BaseModel):
    email: EmailStr
    token: str

@router.post("/auth/verify-signup-otp", response_model=AuthResponse)
async def verify_signup_otp(body: VerifySignupOtpRequest):
    """Verify OTP for new account signup - no password update, just return tokens."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/auth/v1/verify",
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": body.email, "token": body.token, "type": "signup"},
        )
    data = resp.json()
    if resp.status_code != 200 or not data.get("access_token"):
        msg = data.get("msg") or data.get("error_description") or "Invalid or expired OTP"
        raise HTTPException(status_code=400, detail=msg)

    return AuthResponse(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        user={
            "id": data["user"]["id"],
            "email": data["user"]["email"],
            "full_name": data["user"].get("user_metadata", {}).get("full_name"),
        },
    )

@router.post("/auth/forgot-password")
async def forgot_password(body: ForgotPasswordRequest):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/auth/v1/otp",
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": body.email, "create_user": False},
        )
    if resp.status_code == 429:
        raise HTTPException(status_code=429, detail="Too many attempts. Please wait a few minutes and try again.")
    return {"message": "If an account exists for this email, a 6-digit OTP has been sent."}

@router.post("/auth/verify-otp")
async def verify_otp(body: VerifyOtpRequest):
    # Step 1: verify OTP to get a session
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/auth/v1/verify",
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": body.email, "token": body.token, "type": "email"},
        )
    data = resp.json()
    if resp.status_code != 200 or not data.get("access_token"):
        msg = data.get("msg") or data.get("error_description") or "Invalid or expired OTP"
        raise HTTPException(status_code=400, detail=msg)

    session_token = data["access_token"]

    # Step 2: use session token to update password
    async with httpx.AsyncClient() as client:
        update = await client.put(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {session_token}",
                "Content-Type": "application/json",
            },
            json={"password": body.password},
        )
    if update.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to update password")

    return {"message": "Password updated successfully"}

@router.post("/auth/signin", response_model=AuthResponse)
async def sign_in(body: SignInRequest):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": body.email, "password": body.password},
        )
    data = resp.json()
    if resp.status_code != 200 or data.get("error"):
        msg = data.get("error_description") or data.get("msg") or "Invalid credentials"
        raise HTTPException(status_code=401, detail=msg)
    return AuthResponse(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        user={
            "id": data["user"]["id"],
            "email": data["user"]["email"],
            "full_name": data["user"].get("user_metadata", {}).get("full_name"),
        },
    )

@router.post("/auth/refresh")
async def refresh_token(body: RefreshRequest):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token",
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"refresh_token": body.refresh_token},
        )
    data = resp.json()
    if resp.status_code != 200 or "error" in data:
        raise HTTPException(status_code=401, detail="Refresh token invalid or expired")
    return {"access_token": data["access_token"], "refresh_token": data["refresh_token"]}

@router.post("/auth/signout")
async def sign_out(current_user: dict = Depends(get_current_user)):
    return {"message": "Signed out successfully"}

@router.get("/api/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    metadata = current_user.get("user_metadata", {})
    # OAuth providers (Google/Microsoft/GitHub) don't always set "full_name" -
    # fall back to the provider-supplied "name" or GitHub's "user_name".
    full_name = metadata.get("full_name") or metadata.get("name") or metadata.get("user_name")
    return {
        "id": current_user.get("sub"),
        "email": current_user.get("email"),
        "full_name": full_name,
        "avatar_url": metadata.get("avatar_url") or metadata.get("picture"),
        "role": current_user.get("role", "authenticated"),
    }
