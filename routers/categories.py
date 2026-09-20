from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

from auth import get_current_user
from controllers import controller
from database import get_db
from models import User
import schemas


router = APIRouter(
    prefix="/skypdv",
    tags=["skypdv-categories"],
)


@router.get("/categories", response_model=List[str])
def list_categories(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Listar todas as categorias de produtos."""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.get_product_categories(db, terminal.id)


@router.get("/categories-list", response_model=List[schemas.PDVCategory])
def list_categories_full(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Listar categorias cadastradas com totais dos produtos disponiveis."""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.get_categories_list(db, terminal.id)


@router.post("/categories-list", response_model=schemas.PDVCategory)
def create_category(
    category: schemas.PDVCategoryCreate,
    is_global: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Criar nova categoria."""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.create_category(db, category, terminal.id, current_user.id, is_global)


@router.post("/categories-list/{category_id}/adopt", response_model=schemas.PDVCategory)
def adopt_category(
    category_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Adotar uma categoria global para o terminal."""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.adopt_category(db, category_id, terminal.id, current_user.id)


@router.patch("/categories-list/{category_id}", response_model=schemas.PDVCategory)
@router.put("/categories-list/{category_id}", response_model=schemas.PDVCategory)
def update_category(
    category_id: int,
    updates: schemas.PDVCategoryUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Atualizar categoria."""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.update_category(db, category_id, updates, terminal.id)


@router.delete("/categories-list/{category_id}")
def delete_category(
    category_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Desativar categoria."""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.delete_category(db, category_id, terminal.id)
