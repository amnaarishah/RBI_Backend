"""Login, current user, password change and (admin only) user management."""
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import Principal, authorize
from app.core.config import settings
from app.core.database import get_db
from app.core.security import (ROLES, create_access_token, hash_password, validate_new_password,
                               verify_password)
from app.models.user import User
from app.schemas.auth import (ChangePasswordRequest, LoginRequest, TokenResponse, UserCreate, UserOut,
                              UserUpdate)

public_router = APIRouter(prefix="/auth", tags=["Auth"])      # no token needed
router = APIRouter(prefix="/auth", tags=["Auth"])             # token needed (see api/router.py)

# --- brute-force protection: max failed sign-ins per user+address in a time window ---
MAX_FAILURES, WINDOW_SECONDS = 5, 300
_failures: dict = defaultdict(deque)
_DUMMY_HASH = hash_password("not-a-real-password")           # keeps timing similar for unknown users


def _throttle_key(username: str, request: Request) -> str:
    return f"{username.lower()}|{request.client.host if request.client else '-'}"


def _recent_failures(key: str) -> deque:
    q = _failures[key]
    cutoff = time.monotonic() - WINDOW_SECONDS
    while q and q[0] < cutoff:
        q.popleft()
    return q


@public_router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, request: Request, db: Session = Depends(get_db)):
    key = _throttle_key(data.username, request)
    if len(_recent_failures(key)) >= MAX_FAILURES:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="Too many failed attempts. Please wait a few minutes and try again.")
    user = db.query(User).filter(User.username == data.username.strip().lower()).first()
    ok = verify_password(data.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok or not user.is_active:
        _failures[key].append(time.monotonic())
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Wrong username or password.")
    _failures.pop(key, None)
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    return TokenResponse(
        access_token=create_access_token(user.username, user.role),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut)
def me(principal: Principal = Depends(authorize), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == principal.username).first()
    if user is None:     # AUTH_REQUIRED=false (anonymous principal)
        return UserOut(id="00000000-0000-0000-0000-000000000000", username=principal.username,
                       full_name=None, role=principal.role, is_active=True)
    return user


@router.post("/change-password")
def change_password(data: ChangePasswordRequest, principal: Principal = Depends(authorize),
                    db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == principal.username).first()
    if user is None or not verify_password(data.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Current password is wrong.")
    problem = validate_new_password(data.new_password)
    if problem:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=problem)
    user.password_hash = hash_password(data.new_password)
    db.commit()
    return {"status": "ok"}


# ---------------------------------------------------------------- admin: users
@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db)):
    return db.query(User).order_by(User.username).all()


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(data: UserCreate, db: Session = Depends(get_db)):
    if data.role not in ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Role must be one of: {', '.join(ROLES)}.")
    problem = validate_new_password(data.password)
    if problem:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=problem)
    username = data.username.lower()
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="This username already exists.")
    user = User(username=username, full_name=data.full_name, role=data.role,
                password_hash=hash_password(data.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{username}", response_model=UserOut)
def update_user(username: str, data: UserUpdate, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username.lower()).first()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found.")
    changes = data.model_dump(exclude_unset=True)
    if "role" in changes and changes["role"] not in ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Role must be one of: {', '.join(ROLES)}.")
    # never lock the last admin out
    losing_admin = user.role == "admin" and (
        changes.get("role", "admin") != "admin" or changes.get("is_active") is False)
    if losing_admin:
        other_admins = db.query(User).filter(
            User.role == "admin", User.is_active.is_(True), User.id != user.id).count()
        if other_admins == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="You cannot remove the last active admin.")
    if changes.get("new_password"):
        problem = validate_new_password(changes["new_password"])
        if problem:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=problem)
        user.password_hash = hash_password(changes["new_password"])
    for field in ("full_name", "role", "is_active"):
        if field in changes:
            setattr(user, field, changes[field])
    db.commit()
    db.refresh(user)
    return user
