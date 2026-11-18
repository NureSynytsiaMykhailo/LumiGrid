import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import audit
from app.db import get_db
from app.models import User
from app.schemas import UserCreate, UserOut, UserUpdate
from app.security import hash_password, require_roles

router = APIRouter(prefix="/users", tags=["users"])
admin_only = require_roles("admin")

@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(admin_only)):
    return db.scalars(select(User).order_by(User.created_at)).all()

@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(data: UserCreate, db: Session = Depends(get_db),
                admin: User = Depends(admin_only)):
    user = User(email=data.email, password_hash=hash_password(data.password),
                full_name=data.full_name, role=data.role, locale=data.locale)
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Користувач з таким email вже існує")
    audit(db, admin.id, "USER_CREATED", "user", user.id, {"role": user.role})
    db.commit()
    return user

@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: uuid.UUID, data: UserUpdate, db: Session = Depends(get_db),
                admin: User = Depends(admin_only)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Користувача не знайдено")
    changes = data.model_dump(exclude_unset=True)
    if "password" in changes:
        user.password_hash = hash_password(changes.pop("password"))
    for field, value in changes.items():
        setattr(user, field, value)
    audit(db, admin.id, "USER_UPDATED", "user", user.id, changes)
    db.commit()
    return user