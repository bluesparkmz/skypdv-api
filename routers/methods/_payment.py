"""Payment methods owned by the authenticated company/terminal."""

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import schemas
from auth import get_current_user
from controllers import controller
from database import get_db
from models import User


router = APIRouter(prefix="/skypdv/payment-methods", tags=["skypdv-payment-methods"])


@router.get("", response_model=List[schemas.PDVPaymentMethod])
def list_payment_methods(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.get_payment_methods_list(db, terminal.id)


@router.post("", response_model=schemas.PDVPaymentMethod, status_code=201)
def create_payment_method(
    method: schemas.PDVPaymentMethodCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    terminal = controller.get_terminal_required(db, current_user.id)
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_manage_products")
    return controller.create_payment_method(db, method, terminal.id, current_user.id)


@router.put("/{method_id}", response_model=schemas.PDVPaymentMethod)
def update_payment_method(
    method_id: int,
    updates: schemas.PDVPaymentMethodUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    terminal = controller.get_terminal_required(db, current_user.id)
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_manage_products")
    return controller.update_payment_method(db, method_id, updates, terminal.id)


@router.delete("/{method_id}")
def delete_payment_method(
    method_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    terminal = controller.get_terminal_required(db, current_user.id)
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_manage_products")
    return controller.delete_payment_method(db, method_id, terminal.id)
