import os
from datetime import datetime

import httpx
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from database import get_db
from models import User, PDVTerminalInvite, PDVTerminalUser


BLUESPARK_ACCOUNTS_URL = os.getenv("BLUESPARK_ACCOUNTS_URL", "https://accounts.bluesparkmz.com").rstrip("/")
BLUESPARK_PRODUCT_CODE = os.getenv("BLUESPARK_PRODUCT_CODE", "skypdv")
BLUESPARK_AUTH_TIMEOUT_SECONDS = float(os.getenv("BLUESPARK_AUTH_TIMEOUT_SECONDS", "15"))

bearer_scheme = HTTPBearer(scheme_name="BlueSparkAccounts")


def _accounts_url(path: str) -> str:
    return f"{BLUESPARK_ACCOUNTS_URL}{path}"


def _accounts_request(method: str, path: str, json: dict | None = None, token: str | None = None) -> dict:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        with httpx.Client(timeout=BLUESPARK_AUTH_TIMEOUT_SECONDS) as client:
            response = client.request(method, _accounts_url(path), json=json, headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="BlueSpark auth indisponivel no momento.",
        ) from exc

    data = response.json() if response.content else {}
    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=data.get("detail", "Falha na auth central."),
        )
    return data


def introspect_central_token(token: str) -> dict:
    claims = _accounts_request(
        "POST",
        "/auth/introspect",
        json={
            "token": token,
            "product_code": BLUESPARK_PRODUCT_CODE,
        },
    )
    if not claims.get("active"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessao invalida.")
    return claims


def _has_skypdv_membership(claims: dict) -> bool:
    if claims.get("product_code") == BLUESPARK_PRODUCT_CODE or claims.get("aud") == BLUESPARK_PRODUCT_CODE:
        return True
    for membership in claims.get("memberships") or []:
        if membership.get("product") == BLUESPARK_PRODUCT_CODE and membership.get("status") == "active":
            return True
    return False


def sync_local_user_from_claims(db: Session, claims: dict) -> User:
    central_user_id = str(claims.get("central_user_id") or claims.get("sub") or "").strip()
    email = (claims.get("email") or "").strip().lower()
    username = (claims.get("username") or email.split("@")[0] or f"user{central_user_id}").strip()
    full_name = (claims.get("name") or claims.get("full_name") or username).strip()

    if not central_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token central sem utilizador.")
    if not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token central sem email.")

    user = db.query(User).filter(User.central_user_id == int(central_user_id)).first()
    if not user:
        user = db.query(User).filter(User.email == email).first()

    if user:
        user.central_user_id = int(central_user_id)
        user.email = email
        user.username = username
        user.name = full_name
        user.phone = claims.get("phone") or user.phone
        user.is_active = True
        user.is_verified = bool(claims.get("is_verified", user.is_verified))
        user.profile_image_url = claims.get("profile_image_url") or user.profile_image_url
        user.raw_claims = claims
        user.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(user)
        return user

    user = User(
        central_user_id=int(central_user_id),
        email=email,
        username=username,
        name=full_name,
        phone=claims.get("phone"),
        is_active=True,
        is_verified=bool(claims.get("is_verified", True)),
        profile_image_url=claims.get("profile_image_url"),
        raw_claims=claims,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def accept_pending_terminal_invites(db: Session, user: User) -> None:
    invites = db.query(PDVTerminalInvite).filter(
        PDVTerminalInvite.invited_email == user.email,
        PDVTerminalInvite.is_active == True,
        PDVTerminalInvite.accepted_at.is_(None),
    ).all()

    changed = False
    for invite in invites:
        existing = db.query(PDVTerminalUser).filter(
            PDVTerminalUser.terminal_id == invite.terminal_id,
            PDVTerminalUser.user_id == user.id,
        ).first()
        if not existing:
            membership = PDVTerminalUser(
                terminal_id=invite.terminal_id,
                user_id=user.id,
                role=invite.role,
                can_sell=invite.can_sell,
                can_open_cash_register=invite.can_open_cash_register,
                can_manage_products=invite.can_manage_products,
                can_manage_stock=invite.can_manage_stock,
                can_view_reports=invite.can_view_reports,
                can_manage_users=invite.can_manage_users,
                invited_by=invite.invited_by,
                invited_at=invite.invited_at,
                joined_at=datetime.utcnow(),
                is_active=invite.is_active,
            )
            db.add(membership)
            changed = True

        invite.accepted_at = datetime.utcnow()
        invite.is_active = False
        invite.updated_at = datetime.utcnow()
        changed = True

    if changed:
        db.commit()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    token = (credentials.credentials or "").strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token ausente")

    claims = introspect_central_token(token)
    if not _has_skypdv_membership(claims):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sem acesso ao produto SkyPDV")

    user = sync_local_user_from_claims(db, claims)
    accept_pending_terminal_invites(db, user)
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Utilizador inativo")
    return user
