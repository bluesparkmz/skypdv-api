from datetime import datetime

import os
from fastapi import Depends, FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from auth import get_current_user
from database import Base, engine, get_db
from models import PDVProduct, User, FastFoodRestaurant, RestaurantTable
from routers.sky_pdv_router import router as sky_pdv_router
from controllers import controller
import schemas

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


@app.post("/user/phone")
def update_phone(
    phone: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Simples: atualizar telefone do usuário autenticado
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        return {"status": "error", "message": "User not found"}
    user.phone = phone
    db.add(user)
    db.commit()
    return {"status": "ok", "phone": phone}


app.include_router(sky_pdv_router)

# FastFood compatibility routes without /skypdv prefix (frontend legacy)
def _get_or_create_fastfood_restaurant(db: Session, current_user: User) -> FastFoodRestaurant:
    restaurant = db.query(FastFoodRestaurant).filter(FastFoodRestaurant.user_id == current_user.id).first()
    if restaurant:
        return restaurant

    terminal = controller.get_or_create_terminal(db, current_user.id, create_if_missing=True)
    fallback_name = terminal.name if terminal else (current_user.name or "Restaurante")

    restaurant = FastFoodRestaurant(
        user_id=current_user.id,
        name=fallback_name,
        category="restaurant",
        is_open=False,
        active=True,
        phone=terminal.phone if terminal else None,
        address=terminal.address if terminal else None,
    )
    db.add(restaurant)
    db.commit()
    db.refresh(restaurant)
    return restaurant


@app.post("/fastfood/restaurants")
async def create_fastfood_restaurant_root(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    data = await request.form()
    name = data.get("name") or "FastFood"
    restaurant = FastFoodRestaurant(
        user_id=current_user.id,
        name=name,
        category=data.get("category") or "restaurant",
        is_open=False,
        active=True,
        phone=data.get("phone"),
        address=data.get("address"),
    )
    db.add(restaurant)
    db.commit()
    db.refresh(restaurant)
    return restaurant


@app.get("/fastfood/restaurants/mine")
def list_my_restaurants_root(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    restaurant = _get_or_create_fastfood_restaurant(db, current_user)
    return [restaurant]


@app.get("/fastfood/restaurants/{restaurant_id}/tables")
def list_tables(
    restaurant_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    restaurant = db.query(FastFoodRestaurant).filter(
        FastFoodRestaurant.id == restaurant_id,
        FastFoodRestaurant.user_id == current_user.id,
    ).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    return (
        db.query(RestaurantTable)
        .filter(RestaurantTable.restaurant_id == restaurant.id)
        .order_by(RestaurantTable.id)
        .all()
    )


@app.post("/fastfood/restaurants/{restaurant_id}/tables", status_code=201)
def create_table(
    restaurant_id: int,
    table: schemas.RestaurantTableCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    restaurant = db.query(FastFoodRestaurant).filter(
        FastFoodRestaurant.id == restaurant_id,
        FastFoodRestaurant.user_id == current_user.id,
    ).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    new_table = RestaurantTable(
        restaurant_id=restaurant.id,
        table_number=table.table_number,
        seats=table.seats,
        shape=table.shape,
        width=table.width,
        height=table.height,
        position_x=table.position_x,
        position_y=table.position_y,
        status=table.status or "available",
    )
    db.add(new_table)
    db.commit()
    db.refresh(new_table)
    return new_table


@app.put("/fastfood/restaurants/{restaurant_id}/tables/{table_id}")
def update_table(
    restaurant_id: int,
    table_id: int,
    data: schemas.RestaurantTableUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    restaurant = db.query(FastFoodRestaurant).filter(
        FastFoodRestaurant.id == restaurant_id,
        FastFoodRestaurant.user_id == current_user.id,
    ).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    table = (
        db.query(RestaurantTable)
        .filter(RestaurantTable.restaurant_id == restaurant.id, RestaurantTable.id == table_id)
        .first()
    )
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(table, field, value)
    db.add(table)
    db.commit()
    db.refresh(table)
    return table


@app.patch("/fastfood/restaurants/{restaurant_id}/tables/{table_id}/position")
def update_table_position(
    restaurant_id: int,
    table_id: int,
    data: schemas.RestaurantTablePosition,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    restaurant = db.query(FastFoodRestaurant).filter(
        FastFoodRestaurant.id == restaurant_id,
        FastFoodRestaurant.user_id == current_user.id,
    ).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    table = (
        db.query(RestaurantTable)
        .filter(RestaurantTable.restaurant_id == restaurant.id, RestaurantTable.id == table_id)
        .first()
    )
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    table.position_x = data.position_x
    table.position_y = data.position_y
    db.add(table)
    db.commit()
    db.refresh(table)
    return table


@app.delete("/fastfood/restaurants/{restaurant_id}/tables/{table_id}")
def delete_table(
    restaurant_id: int,
    table_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    restaurant = db.query(FastFoodRestaurant).filter(
        FastFoodRestaurant.id == restaurant_id,
        FastFoodRestaurant.user_id == current_user.id,
    ).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    table = (
        db.query(RestaurantTable)
        .filter(RestaurantTable.restaurant_id == restaurant.id, RestaurantTable.id == table_id)
        .first()
    )
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    db.delete(table)
    db.commit()
    return {"message": "Table deleted"}
