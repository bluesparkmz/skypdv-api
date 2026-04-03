from datetime import datetime

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from auth import get_current_user
from database import Base, engine, get_db
from models import PDVProduct, User
from routers.sky_pdv_router import router as sky_pdv_router

import models  # noqa: F401


Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="SkyPDV API",
    version="0.1.0",
    description="Backend independente do SkyPDV com autenticacao via BlueSpark Accounts.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "https://skypdv.bluesparkmz.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

