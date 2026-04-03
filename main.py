from datetime import datetime

import os
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from auth import get_current_user
from database import Base, engine, get_db
from models import PDVProduct, User
from routers.sky_pdv_router import router as sky_pdv_router
from controllers import controller

import models  # noqa: F401

def _build_cors_origins():
    raw = os.getenv("CORS_ORIGINS", "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "https://skypdv.bluesparkmz.com",
        "https://skypdv.skyvenda.com",
        "https://skypdvmz.bluesparkmz.com",
        "https://skypdv.vercel.app",
    ]


Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="SkyPDV API",
    version="0.1.0",
    description="Backend independente do SkyPDV com autenticacao via BlueSpark Accounts.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://.*",
    allow_origins=_build_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=86400,
)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "skypdv-api",
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/user/profile")
def user_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    terminal = getattr(current_user, "pdv_terminal", None)
    # pdv_terminal pode ser lista (relationship one-to-many). Pegamos o primeiro.
    if isinstance(terminal, list):
        terminal = terminal[0] if terminal else None
    total_products = 0
    if terminal:
        total_products = (
            db.query(PDVProduct)
            .filter(PDVProduct.terminal_id == terminal.id, PDVProduct.is_active == True)
            .count()
        )

    return {
        "context": "self",
        "is_authenticated": True,
        "is_me": True,
        "can_edit": True,
        "is_following": False,
        "is_follower": False,
        "user": {
            "id": current_user.id,
            "username": current_user.username,
            "email": current_user.email,
            "name": current_user.name,
            "phone": current_user.phone,
            "active": current_user.is_active,
            "profile_image": current_user.profile_image_url,
            "verification_status": "verified" if current_user.is_verified else "unverified",
        },
        "stats": {
            "total_products": total_products,
            "total_followers": 0,
            "total_following": 0,
        },
    }


app.include_router(sky_pdv_router)

# FastFood compatibility routes without /skypdv prefix (frontend legacy)
@app.post("/fastfood/restaurants")
async def create_fastfood_restaurant_root(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    data = await request.form()
    name = data.get("name") or "FastFood"
    return {
        "id": 1,
        "user_id": current_user.id,
        "name": name,
        "category": data.get("category") or "restaurant",
        "is_open": False,
        "active": True,
        "phone": data.get("phone"),
        "address": data.get("address"),
    }


@app.get("/fastfood/restaurants/mine")
def list_my_restaurants_root(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Reaproveita a mesma lógica de autocriação de terminal para forçar vínculo
    controller.get_or_create_terminal(db, current_user.id, create_if_missing=True)
    return []
