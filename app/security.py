import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException, status, FastAPI
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Luminaire, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
ALGORITHM = "HS256"

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def create_access_token(user: User) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": str(user.id), "role": user.role, "exp": expires}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)

def _unauthorized(detail: str = "Недійсні облікові дані") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})

def get_current_user(token: str = Depends(oauth2_scheme),
                     db: Session = Depends(get_db)) -> User:
    try:
        data = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
        user = db.get(User, uuid.UUID(data["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise _unauthorized()
    if user is None or not user.is_active:
        raise _unauthorized()
    return user

def require_roles(*roles: str):
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостатньо прав")
        return user
    return checker

def get_device(x_device_serial: str = Header(), x_device_token: str = Header(),
               db: Session = Depends(get_db)) -> Luminaire:
    lum = db.scalar(select(Luminaire).where(Luminaire.serial_number == x_device_serial))
    if (lum is None or lum.is_archived
        or not hmac.compare_digest(lum.device_token_hash, hash_token(x_device_token))):
        raise _unauthorized("Недійсні облікові дані пристрою")
    return lum