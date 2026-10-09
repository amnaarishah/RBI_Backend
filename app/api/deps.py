"""Authentication and role-based authorization for every protected route.

Roles (low -> high):  viewer < engineer < admin
  viewer    read everything, run calculations
  engineer  + create / edit equipment, inspections, imports, uploads
  admin     + delete, manage users
The rule is decided in ONE place (required_role) from the HTTP method and path, so a new endpoint is
protected automatically.
"""
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.user import User

ROLE_RANK = {"viewer": 1, "engineer": 2, "admin": 3}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
COMPUTE_ONLY_POSTS = {"/api/v1/asme/calculate-tmin", "/api/v1/tank/calculate"}          # calculations that store nothing
SELF_SERVICE_PATHS = {"/api/v1/auth/change-password"}          # any signed-in user
ADMIN_PREFIX = "/api/v1/auth/users"

bearer = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    username: str
    role: str
    full_name: Optional[str] = None
    user_id: Optional[str] = None


def required_role(method: str, path: str) -> str:
    path = path.rstrip("/")
    if path.startswith(ADMIN_PREFIX):
        return "admin"
    if path in SELF_SERVICE_PATHS or path in COMPUTE_ONLY_POSTS or method.upper() in SAFE_METHODS:
        return "viewer"
    if method.upper() == "DELETE":
        return "admin"
    return "engineer"


def authorize(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: Session = Depends(get_db),
) -> Principal:
    if not settings.AUTH_REQUIRED:
        return Principal(username="System", role="admin")

    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Please sign in.",
                                 headers={"WWW-Authenticate": "Bearer"})
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized
    payload = decode_access_token(credentials.credentials)
    if not payload or not payload.get("sub"):
        raise unauthorized

    user = db.query(User).filter(User.username == str(payload["sub"]).lower()).first()
    if user is None or not user.is_active:
        raise unauthorized

    need = required_role(request.method, request.url.path)
    if ROLE_RANK.get(user.role, 0) < ROLE_RANK[need]:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail=f"Your role ({user.role}) is not allowed to do this. Required: {need}.")
    return Principal(username=user.username, role=user.role, full_name=user.full_name, user_id=str(user.id))
