from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional, Any
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, or_
from fastapi import HTTPException, status, UploadFile

from models import (
    User, PDVTerminal, PDVTerminalUser, PDVTerminalRole, PDVSupplier, PDVProduct, PDVInventory,
    PDVStockMovement, PDVCashRegister, PDVSale, PDVSaleItem,
    SourceType, MovementType, PaymentMethod, SaleType,
    PDVCategory, PDVPaymentMethod, PDVExpenseCategory, PDVExpense, PDVTerminalInvite
)
import schemas

Restaurant = None
Product = None
FastFoodTab = None
FastFoodOrder = None
FastFoodOrderItem = None

PRIMARY_STOCK_LOCATION = "balcao"

# ===================================================================
# Terminals
# ===================================================================

def create_terminal_for_user(db: Session, user_id: int, data: schemas.PDVTerminalCreate | None = None):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    existing = db.query(PDVTerminal).filter(PDVTerminal.user_id == user_id).first()
    if existing:
        return existing

    terminal_name = None
    if data and getattr(data, "name", None):
        terminal_name = data.name
    else:
        terminal_name = f"Loja de {user.name}" if user.name else f"PDV Terminal {user_id}"

    terminal = PDVTerminal(
        user_id=user_id,
        name=terminal_name,
        description=data.description if data else None,
        logo=data.logo if data else None,
        bio=data.bio if data else None,
        active=True,
        tax_rate=data.tax_rate if data and data.tax_rate is not None else None,
        currency=data.currency if data and data.currency else None,
        settings=data.settings if data else None,
    )

    db.add(terminal)
    db.commit()
    db.refresh(terminal)

    # Verificar se é um estabelecimento FastFood e criar restaurante automaticamente
    business_type = None
    if data and data.settings and isinstance(data.settings, dict):
        business_type = data.settings.get("business_type")
    
    # Tipos que são FastFood: restaurant, cafeteria, snackbar
    is_fastfood_type = business_type in ["restaurant", "cafeteria", "snackbar"]
    
    if is_fastfood_type and Restaurant is not None:
        try:
            from fastfood.controller import generate_restaurant_slug
            
            # Verificar se já existe restaurante para este usuário
            existing_restaurant = db.query(Restaurant).filter(Restaurant.user_id == user_id).first()
            if not existing_restaurant:
                # Criar restaurante FastFood automaticamente
                restaurant_name = terminal_name or (user.name if user.name else f"Estabelecimento {user_id}")
                
                # Gerar slug único
                restaurant_slug = generate_restaurant_slug(db, restaurant_name)
                
                # Criar restaurante
                db_restaurant = Restaurant(
                    user_id=user_id,
                    name=restaurant_name,
                    slug=restaurant_slug,
                    province=None,
                    district=None,
                    neighborhood=None,
                    avenue=None,
                    location_google_maps=None,
                    opening_time=None,
                    closing_time=None,
                    open_days=None,
                    min_delivery_value=Decimal("0.00"),
                    latitude=None,
                    longitude=None,
                    cover_image=None,
                    images=None,
                    is_open=False,  # Começar fechado, usuário pode abrir depois
                    active=True
                )
                db.add(db_restaurant)
                db.commit()
                db.refresh(db_restaurant)
                
                # Conectar restaurante ao terminal
                connect_fastfood_restaurant(db, terminal.id, db_restaurant.id, sync=True)
                print(f"SkyPDV: Restaurante FastFood criado automaticamente: {db_restaurant.name} (ID: {db_restaurant.id})")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"SkyPDV: Erro ao criar restaurante FastFood automaticamente: {e}")
            # Continuar mesmo se falhar - o terminal já foi criado

    local_supplier = PDVSupplier(
        terminal_id=terminal.id,
        name="Produtos Locais",
        source_type=SourceType.LOCAL,
        is_active=True,
    )
    db.add(local_supplier)
    db.commit()

    # Criar categorias padrão
    default_categories = [
        {"name": "Alimentos", "icon": "🍔", "color": "#10b981"},
        {"name": "Bebidas", "icon": "🥤", "color": "#3b82f6"},
        {"name": "Eletrônicos", "icon": "📱", "color": "#8b5cf6"},
        {"name": "Vestuário", "icon": "👕", "color": "#ec4899"},
        {"name": "Higiene", "icon": "🧴", "color": "#06b6d4"},
        {"name": "Limpeza", "icon": "🧹", "color": "#f59e0b"},
        {"name": "Papelaria", "icon": "📝", "color": "#6366f1"},
        {"name": "Farmácia", "icon": "💊", "color": "#ef4444"},
        {"name": "Outros", "icon": "📦", "color": "#64748b"},
    ]
    
    for cat_data in default_categories:
        category = PDVCategory(
            terminal_id=terminal.id,
            name=cat_data["name"],
            icon=cat_data["icon"],
            color=cat_data["color"],
            is_global=False,
            is_active=True,
        )
        db.add(category)

    default_expense_categories = [
        {"name": "Renda da Loja", "code": "aluguel", "icon": "store", "color": "#ef4444"},
        {"name": "Salário", "code": "salario", "icon": "users", "color": "#f59e0b"},
        {"name": "Internet", "code": "internet", "icon": "wifi", "color": "#3b82f6"},
        {"name": "Combustível", "code": "combustivel", "icon": "truck", "color": "#10b981"},
        {"name": "Fornecedor", "code": "fornecedor", "icon": "package", "color": "#8b5cf6"},
        {"name": "Energia", "code": "energia", "icon": "bolt", "color": "#06b6d4"},
        {"name": "Água", "code": "agua", "icon": "droplet", "color": "#0ea5e9"},
        {"name": "Outras Despesas", "code": "outros", "icon": "receipt", "color": "#64748b"},
    ]

    for item in default_expense_categories:
        db.add(
            PDVExpenseCategory(
                terminal_id=terminal.id,
                created_by=user_id,
                name=item["name"],
                code=item["code"],
                description=None,
                icon=item["icon"],
                color=item["color"],
                is_global=False,
                is_active=True,
            )
        )
    
    db.commit()

    return terminal


def get_or_create_terminal(db: Session, user_id: int, create_if_missing: bool = True):
    """Obter terminal para o usuário.

    - Se for dono de um terminal: retorna.
    - Se estiver associado a um terminal: retorna.
    - Se não existir:
        - create_if_missing=True: cria
        - create_if_missing=False: retorna None
    """
    terminal = db.query(PDVTerminal).filter(PDVTerminal.user_id == user_id).first()

    if not terminal:
        terminal_user = db.query(PDVTerminalUser).filter(
            PDVTerminalUser.user_id == user_id,
            PDVTerminalUser.is_active == True,
        ).first()

        if terminal_user:
            terminal = db.query(PDVTerminal).filter(PDVTerminal.id == terminal_user.terminal_id).first()
            if terminal:
                return terminal

    if not terminal and create_if_missing:
        terminal = create_terminal_for_user(db, user_id)

    if not terminal:
        return None

    if Restaurant is not None:
        try:
            restaurant = db.query(Restaurant).filter(Restaurant.user_id == user_id).first()
            if restaurant:
                connect_fastfood_restaurant(db, terminal.id, restaurant.id, sync=True)
        except Exception as e:
            print(f"SkyPDV: Erro ao auto-conectar restaurante: {e}")

    return terminal


def get_terminal_required(db: Session, user_id: int):
    terminal = get_or_create_terminal(db, user_id, create_if_missing=False)
    if not terminal:
        raise HTTPException(status_code=404, detail="PDV not setup")
    return terminal

def update_terminal(db: Session, terminal_id: int, updates: schemas.PDVTerminalUpdate, user_id: int):
    terminal = db.query(PDVTerminal).filter(PDVTerminal.id == terminal_id).first()
    if not terminal:
        raise HTTPException(status_code=404, detail="Terminal not found")
        
    if terminal.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    for field, value in updates.dict(exclude_unset=True).items():
        setattr(terminal, field, value)
        
    db.commit()
    db.refresh(terminal)
    return terminal


# ===================================================================
# Terminal Users Management
# ===================================================================

def is_terminal_admin(db: Session, terminal_id: int, user_id: int) -> bool:
    """Verifica se um usuário é admin do terminal (dono ou com role ADMIN)"""
    terminal = db.query(PDVTerminal).filter(PDVTerminal.id == terminal_id).first()
    if not terminal:
        return False
    
    # Dono do terminal é sempre admin
    if terminal.user_id == user_id:
        return True
    
    # Verificar se é usuário associado com role ADMIN
    terminal_user = db.query(PDVTerminalUser).filter(
        PDVTerminalUser.terminal_id == terminal_id,
        PDVTerminalUser.user_id == user_id,
        PDVTerminalUser.is_active == True
    ).first()
    
    if terminal_user and terminal_user.role == PDVTerminalRole.ADMIN:
        return True
    
    return False

def check_terminal_permission(db: Session, terminal_id: int, user_id: int, permission: str) -> bool:
    """Verifica se um usuário tem permissão específica no terminal"""
    terminal = db.query(PDVTerminal).filter(PDVTerminal.id == terminal_id).first()
    if not terminal:
        return False
    
    # Dono do terminal tem todas as permissões
    if terminal.user_id == user_id:
        return True
    
    # Verificar se é usuário associado
    terminal_user = db.query(PDVTerminalUser).filter(
        PDVTerminalUser.terminal_id == terminal_id,
        PDVTerminalUser.user_id == user_id,
        PDVTerminalUser.is_active == True
    ).first()
    
    if not terminal_user:
        return False
    
    # Se role é ADMIN, tem todas as permissões
    if terminal_user.role == PDVTerminalRole.ADMIN:
        return True
    
    # Verificar permissão específica
    permission_map = {
        "can_sell": terminal_user.can_sell,
        "can_open_cash_register": terminal_user.can_open_cash_register,
        "can_manage_products": terminal_user.can_manage_products,
        "can_manage_stock": terminal_user.can_manage_stock,
        "can_view_reports": terminal_user.can_view_reports,
        "can_manage_users": terminal_user.can_manage_users,
    }
    
    return permission_map.get(permission, False)


def require_terminal_permission(db: Session, terminal_id: int, user_id: int, permission: str, detail: str | None = None) -> None:
    if not check_terminal_permission(db, terminal_id, user_id, permission):
        raise HTTPException(status_code=403, detail=detail or "You don't have permission to perform this action")


def get_primary_inventory(db: Session, product_id: int, terminal_id: int, create_if_missing: bool = True) -> Optional[PDVInventory]:
    inventory = db.query(PDVInventory).filter(
        PDVInventory.product_id == product_id,
        PDVInventory.terminal_id == terminal_id,
        PDVInventory.storage_location == PRIMARY_STOCK_LOCATION,
    ).first()

    if inventory or not create_if_missing:
        return inventory

    inventory = PDVInventory(
        product_id=product_id,
        terminal_id=terminal_id,
        storage_location=PRIMARY_STOCK_LOCATION,
        quantity=0,
        min_quantity=0,
        reserved_quantity=0,
    )
    db.add(inventory)
    db.commit()
    db.refresh(inventory)
    return inventory


def get_terminal_users(db: Session, terminal_id: int, user_id: int) -> List[dict]:
    """Lista todos os usuários associados ao terminal"""
    terminal = db.query(PDVTerminal).filter(PDVTerminal.id == terminal_id).first()
    if not terminal:
        raise HTTPException(status_code=404, detail="Terminal not found")
    
    if terminal.user_id != user_id and not check_terminal_permission(db, terminal_id, user_id, "can_manage_users"):
        raise HTTPException(status_code=403, detail="You don't have permission to view terminal users")
    
    terminal_users = db.query(PDVTerminalUser).filter(
        PDVTerminalUser.terminal_id == terminal_id
    ).all()
    pending_invites = db.query(PDVTerminalInvite).filter(
        PDVTerminalInvite.terminal_id == terminal_id,
        PDVTerminalInvite.is_active == True,
        PDVTerminalInvite.accepted_at.is_(None),
    ).all()
    
    # Converter para dict com informações do usuário
    result = []
    for tu in terminal_users:
        user = db.query(User).filter(User.id == tu.user_id).first()
        result.append({
            "id": tu.id,
            "terminal_id": tu.terminal_id,
            "user_id": tu.user_id,
            "role": tu.role.value if hasattr(tu.role, 'value') else str(tu.role),
            "can_sell": tu.can_sell,
            "can_open_cash_register": tu.can_open_cash_register,
            "can_manage_products": tu.can_manage_products,
            "can_manage_stock": tu.can_manage_stock,
            "can_view_reports": tu.can_view_reports,
            "can_manage_users": tu.can_manage_users,
            "is_active": tu.is_active,
            "invited_by": tu.invited_by,
            "invited_at": tu.invited_at,
            "joined_at": tu.joined_at,
            "created_at": tu.created_at,
            "updated_at": tu.updated_at,
            "is_pending": False,
            "invited_email": user.email if user else None,
            "user_name": user.name if user else None,
            "user_email": user.email if user else None,
        })

    for invite in pending_invites:
        result.append({
            "id": invite.id,
            "terminal_id": invite.terminal_id,
            "user_id": None,
            "role": invite.role.value if hasattr(invite.role, "value") else str(invite.role),
            "can_sell": invite.can_sell,
            "can_open_cash_register": invite.can_open_cash_register,
            "can_manage_products": invite.can_manage_products,
            "can_manage_stock": invite.can_manage_stock,
            "can_view_reports": invite.can_view_reports,
            "can_manage_users": invite.can_manage_users,
            "is_active": invite.is_active,
            "invited_by": invite.invited_by,
            "invited_at": invite.invited_at,
            "joined_at": None,
            "created_at": invite.created_at,
            "updated_at": invite.updated_at,
            "is_pending": True,
            "invited_email": invite.invited_email,
            "user_name": None,
            "user_email": invite.invited_email,
        })
    
    return result


def add_terminal_user(db: Session, terminal_id: int, user_email: str, user_data: schemas.PDVTerminalUserCreate, inviter_id: int) -> dict:
    """Adiciona um usuário ao terminal pelo email"""
    terminal = db.query(PDVTerminal).filter(PDVTerminal.id == terminal_id).first()
    if not terminal:
        raise HTTPException(status_code=404, detail="Terminal not found")
    
    if terminal.user_id != inviter_id and not check_terminal_permission(db, terminal_id, inviter_id, "can_manage_users"):
        raise HTTPException(status_code=403, detail="You don't have permission to add users to this terminal")
    
    # Buscar usuário pelo email
    normalized_email = user_email.strip().lower()
    role_map = {
        "admin": PDVTerminalRole.ADMIN.value,
        "cashier": PDVTerminalRole.CASHIER.value,
        "manager": PDVTerminalRole.MANAGER.value,
        "viewer": PDVTerminalRole.VIEWER.value,
    }
    role_value = user_data.role.value if hasattr(user_data.role, 'value') else str(user_data.role)
    role_value = role_value.lower() if role_value else "cashier"
    db_role_value = role_map.get(role_value, PDVTerminalRole.CASHIER.value)
    db_role_enum = PDVTerminalRole(db_role_value)
    user = db.query(User).filter(User.email == normalized_email).first()
    if not user:
        invite = db.query(PDVTerminalInvite).filter(
            PDVTerminalInvite.terminal_id == terminal_id,
            PDVTerminalInvite.invited_email == normalized_email,
        ).first()
        if not invite:
            invite = PDVTerminalInvite(
                terminal_id=terminal_id,
                invited_email=normalized_email,
                invited_by=inviter_id,
            )
            db.add(invite)

        invite.role = db_role_enum
        invite.can_sell = user_data.can_sell
        invite.can_open_cash_register = user_data.can_open_cash_register
        invite.can_manage_products = user_data.can_manage_products
        invite.can_manage_stock = user_data.can_manage_stock
        invite.can_view_reports = user_data.can_view_reports
        invite.can_manage_users = user_data.can_manage_users
        invite.invited_by = inviter_id
        invite.invited_at = datetime.utcnow()
        invite.accepted_at = None
        invite.is_active = True
        invite.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(invite)
        return {
            "id": invite.id,
            "terminal_id": invite.terminal_id,
            "user_id": None,
            "role": invite.role.value if hasattr(invite.role, 'value') else str(invite.role),
            "can_sell": invite.can_sell,
            "can_open_cash_register": invite.can_open_cash_register,
            "can_manage_products": invite.can_manage_products,
            "can_manage_stock": invite.can_manage_stock,
            "can_view_reports": invite.can_view_reports,
            "can_manage_users": invite.can_manage_users,
            "is_active": invite.is_active,
            "invited_by": invite.invited_by,
            "invited_at": invite.invited_at,
            "joined_at": None,
            "created_at": invite.created_at,
            "updated_at": invite.updated_at,
            "is_pending": True,
            "invited_email": invite.invited_email,
            "user_name": None,
            "user_email": invite.invited_email,
        }
    
    # Verificar se já está associado
    existing = db.query(PDVTerminalUser).filter(
        PDVTerminalUser.terminal_id == terminal_id,
        PDVTerminalUser.user_id == user.id
    ).first()
    
    if existing:
        raise HTTPException(status_code=400, detail="User is already associated with this terminal")
    
    # Converter role do schema para o valor do enum do modelo
    # Criar associação
    terminal_user = PDVTerminalUser(
        terminal_id=terminal_id,
        user_id=user.id,
        role=db_role_enum,
        can_sell=user_data.can_sell,
        can_open_cash_register=user_data.can_open_cash_register,
        can_manage_products=user_data.can_manage_products,
        can_manage_stock=user_data.can_manage_stock,
        can_view_reports=user_data.can_view_reports,
        can_manage_users=user_data.can_manage_users,
        invited_by=inviter_id,
        joined_at=datetime.utcnow()
    )
    
    db.add(terminal_user)
    db.commit()
    db.refresh(terminal_user)
    
    return {
        "id": terminal_user.id,
        "terminal_id": terminal_user.terminal_id,
        "user_id": terminal_user.user_id,
        "role": terminal_user.role.value if hasattr(terminal_user.role, 'value') else str(terminal_user.role),
        "can_sell": terminal_user.can_sell,
        "can_open_cash_register": terminal_user.can_open_cash_register,
        "can_manage_products": terminal_user.can_manage_products,
        "can_manage_stock": terminal_user.can_manage_stock,
        "can_view_reports": terminal_user.can_view_reports,
        "can_manage_users": terminal_user.can_manage_users,
        "is_active": terminal_user.is_active,
        "invited_by": terminal_user.invited_by,
        "invited_at": terminal_user.invited_at,
        "joined_at": terminal_user.joined_at,
        "created_at": terminal_user.created_at,
        "updated_at": terminal_user.updated_at,
        "is_pending": False,
        "invited_email": user.email,
        "user_name": user.name,
        "user_email": user.email,
    }


def update_terminal_user(db: Session, terminal_id: int, terminal_user_id: int, updates: schemas.PDVTerminalUserUpdate, updater_id: int) -> dict:
    """Atualiza permissões de um usuário do terminal"""
    terminal = db.query(PDVTerminal).filter(PDVTerminal.id == terminal_id).first()
    if not terminal:
        raise HTTPException(status_code=404, detail="Terminal not found")
    
    if terminal.user_id != updater_id and not check_terminal_permission(db, terminal_id, updater_id, "can_manage_users"):
        raise HTTPException(status_code=403, detail="You don't have permission to update terminal users")
    
    terminal_user = db.query(PDVTerminalUser).filter(
        PDVTerminalUser.id == terminal_user_id,
        PDVTerminalUser.terminal_id == terminal_id
    ).first()
    pending_invite = None
    if not terminal_user:
        pending_invite = db.query(PDVTerminalInvite).filter(
            PDVTerminalInvite.id == terminal_user_id,
            PDVTerminalInvite.terminal_id == terminal_id,
        ).first()
        if not pending_invite:
            raise HTTPException(status_code=404, detail="Terminal user not found")
    
    # Não permitir remover o dono do terminal
    if terminal_user and terminal_user.user_id == terminal.user_id:
        raise HTTPException(status_code=400, detail="Cannot modify the terminal owner")
    
    # Atualizar campos
    if updates.role is not None:
        role_map = {
            "admin": PDVTerminalRole.ADMIN.value,
            "cashier": PDVTerminalRole.CASHIER.value,
            "manager": PDVTerminalRole.MANAGER.value,
            "viewer": PDVTerminalRole.VIEWER.value,
        }
        # Obter o valor string do role (lowercase)
        role_value = updates.role.value if hasattr(updates.role, 'value') else str(updates.role)
        role_value = role_value.lower() if role_value else "cashier"
        db_role_value = role_map.get(role_value, PDVTerminalRole.CASHIER.value)
        # Converter o valor string de volta para o enum member
        if terminal_user:
            terminal_user.role = PDVTerminalRole(db_role_value)
        else:
            pending_invite.role = PDVTerminalRole(db_role_value)
    
    if updates.can_sell is not None:
        (terminal_user or pending_invite).can_sell = updates.can_sell
    if updates.can_open_cash_register is not None:
        (terminal_user or pending_invite).can_open_cash_register = updates.can_open_cash_register
    if updates.can_manage_products is not None:
        (terminal_user or pending_invite).can_manage_products = updates.can_manage_products
    if updates.can_manage_stock is not None:
        (terminal_user or pending_invite).can_manage_stock = updates.can_manage_stock
    if updates.can_view_reports is not None:
        (terminal_user or pending_invite).can_view_reports = updates.can_view_reports
    if updates.can_manage_users is not None:
        (terminal_user or pending_invite).can_manage_users = updates.can_manage_users
    if updates.is_active is not None:
        (terminal_user or pending_invite).is_active = updates.is_active
    
    target = terminal_user or pending_invite
    target.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(target)
    
    user = db.query(User).filter(User.id == terminal_user.user_id).first() if terminal_user else None
    return {
        "id": target.id,
        "terminal_id": target.terminal_id,
        "user_id": terminal_user.user_id if terminal_user else None,
        "role": target.role.value if hasattr(target.role, 'value') else str(target.role),
        "can_sell": target.can_sell,
        "can_open_cash_register": target.can_open_cash_register,
        "can_manage_products": target.can_manage_products,
        "can_manage_stock": target.can_manage_stock,
        "can_view_reports": target.can_view_reports,
        "can_manage_users": target.can_manage_users,
        "is_active": target.is_active,
        "invited_by": target.invited_by,
        "invited_at": target.invited_at,
        "joined_at": terminal_user.joined_at if terminal_user else None,
        "created_at": target.created_at,
        "updated_at": target.updated_at,
        "is_pending": pending_invite is not None,
        "invited_email": pending_invite.invited_email if pending_invite else (user.email if user else None),
        "user_name": user.name if user else None,
        "user_email": pending_invite.invited_email if pending_invite else (user.email if user else None),
    }


def remove_terminal_user(db: Session, terminal_id: int, terminal_user_id: int, remover_id: int):
    """Remove um usuário do terminal"""
    terminal = db.query(PDVTerminal).filter(PDVTerminal.id == terminal_id).first()
    if not terminal:
        raise HTTPException(status_code=404, detail="Terminal not found")
    
    if terminal.user_id != remover_id and not check_terminal_permission(db, terminal_id, remover_id, "can_manage_users"):
        raise HTTPException(status_code=403, detail="You don't have permission to remove terminal users")
    
    terminal_user = db.query(PDVTerminalUser).filter(
        PDVTerminalUser.id == terminal_user_id,
        PDVTerminalUser.terminal_id == terminal_id
    ).first()
    if not terminal_user:
        pending_invite = db.query(PDVTerminalInvite).filter(
            PDVTerminalInvite.id == terminal_user_id,
            PDVTerminalInvite.terminal_id == terminal_id,
        ).first()
        if not pending_invite:
            raise HTTPException(status_code=404, detail="Terminal user not found")
        db.delete(pending_invite)
        db.commit()
        return
    
    # Não permitir remover o dono do terminal
    if terminal_user.user_id == terminal.user_id:
        raise HTTPException(status_code=400, detail="Cannot remove the terminal owner")
    
    db.delete(terminal_user)
    db.commit()

# ===================================================================
# Suppliers
# ===================================================================

def get_suppliers(db: Session, terminal_id: int):
    return db.query(PDVSupplier).filter(PDVSupplier.terminal_id == terminal_id).all()

def create_supplier(db: Session, supplier: schemas.PDVSupplierCreate, terminal_id: int):
    db_supplier = PDVSupplier(
        terminal_id=terminal_id,
        **supplier.dict()
    )
    db.add(db_supplier)
    db.commit()
    db.refresh(db_supplier)
    return db_supplier

def update_supplier(db: Session, supplier_id: int, updates: schemas.PDVSupplierUpdate, terminal_id: int):
    """Atualizar dados do fornecedor"""
    db_supplier = db.query(PDVSupplier).filter(PDVSupplier.id == supplier_id, PDVSupplier.terminal_id == terminal_id).first()
    if not db_supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
        
    update_data = updates.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_supplier, field, value)
        
    db.commit()
    db.refresh(db_supplier)
    return db_supplier

def delete_supplier(db: Session, supplier_id: int, terminal_id: int):
    """Remover fornecedor (Desativa)"""
    db_supplier = db.query(PDVSupplier).filter(PDVSupplier.id == supplier_id, PDVSupplier.terminal_id == terminal_id).first()
    if not db_supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    
    db_supplier.is_active = False
    db.commit()
    return {"message": "Supplier deactivated successfully"}

def connect_fastfood_restaurant(db: Session, terminal_id: int, restaurant_id: int, sync: bool):
    """Conectar um restaurante FastFood como fornecedor e associar produtos FastFood existentes"""
    if Restaurant is None:
        raise HTTPException(status_code=501, detail="FastFood integration is not available in the standalone SkyPDV API yet")
    restaurant = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")
        
    # Verificar se já existe
    existing = db.query(PDVSupplier).filter(
        PDVSupplier.terminal_id == terminal_id,
        PDVSupplier.source_type == SourceType.FASTFOOD,
        PDVSupplier.external_id == restaurant_id
    ).first()
    
    if existing:
        return existing
        
    supplier = PDVSupplier(
        terminal_id=terminal_id,
        name=restaurant.name,
        source_type=SourceType.FASTFOOD,
        external_id=restaurant_id,
        address=f"{restaurant.province or ''}, {restaurant.district or ''}",
        is_active=True
    )
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    
    # Se sync=True, associar produtos FastFood existentes ao fornecedor
    if sync:
        # Associar todos os PDVProducts is_fastfood=true e sem supplier_id a este fornecedor
        # (caso tenham sido criados manualmente antes da conexão)
        fastfood_products = db.query(PDVProduct).filter(
            PDVProduct.terminal_id == terminal_id,
            PDVProduct.is_fastfood == True,
            PDVProduct.supplier_id.is_(None)
        ).all()
        
        for product in fastfood_products:
            product.supplier_id = supplier.id
        
        db.commit()
        db.refresh(supplier)
    
    return supplier

# ===================================================================
# Products & Inventory
# ===================================================================

def get_products(
    db: Session, 
    terminal_id: int, 
    search: str = None, 
    category: str = None, 
    source_type: str = None,
    is_fastfood: bool = None,
    supplier_id: int = None,
    limit: int = 100,
    skip: int = 0
):
    query = db.query(PDVProduct).filter(PDVProduct.terminal_id == terminal_id, PDVProduct.is_active == True)
    
    if search:
        query = query.filter(or_(
            PDVProduct.name.ilike(f"%{search}%"),
            PDVProduct.sku.ilike(f"%{search}%"),
            PDVProduct.barcode.ilike(f"%{search}%")
        ))
        
    if category:
        query = query.filter(PDVProduct.category == category)
        
    if source_type:
        query = query.filter(PDVProduct.source_type == source_type)
        
    if is_fastfood is not None:
        query = query.filter(PDVProduct.is_fastfood == is_fastfood)
        
    if supplier_id:
        query = query.filter(PDVProduct.supplier_id == supplier_id)
        
    products = query.offset(skip).limit(limit).all()
    
    # Anexar informações de estoque
    for p in products:
        if p.track_stock:
            get_primary_inventory(db, p.id, terminal_id, create_if_missing=True)
                
    return products

def get_categories(db: Session, terminal_id: int):
    """Listar categorias únicas usadas no terminal"""
    categories = db.query(PDVProduct.category).filter(
        PDVProduct.terminal_id == terminal_id,
        PDVProduct.category.isnot(None),
        PDVProduct.category != ""
    ).distinct().all()
    return [c[0] for c in categories]


def get_product_categories(db: Session, terminal_id: int):
    """Listar categorias usadas no terminal + categorias geridas ativas."""
    product_categories = get_categories(db, terminal_id)
    managed_categories = db.query(PDVCategory.name).filter(
        or_(PDVCategory.terminal_id == terminal_id, PDVCategory.is_global == True),
        PDVCategory.is_active == True,
    ).all()

    seen = set()
    categories = []
    for value in product_categories + [c[0] for c in managed_categories]:
        if not value or value in seen:
            continue
        seen.add(value)
        categories.append(value)
    return categories

def get_product_stats(db: Session, terminal_id: int):
    """Estatísticas de produtos do terminal"""
    total = db.query(PDVProduct).filter(PDVProduct.terminal_id == terminal_id).count()
    active = db.query(PDVProduct).filter(PDVProduct.terminal_id == terminal_id, PDVProduct.is_active == True).count()
    fastfood = db.query(PDVProduct).filter(PDVProduct.terminal_id == terminal_id, PDVProduct.is_fastfood == True).count()
    local = db.query(PDVProduct).filter(PDVProduct.terminal_id == terminal_id, PDVProduct.is_fastfood == False).count()
    
    categories_count = db.query(PDVProduct.category).filter(
        PDVProduct.terminal_id == terminal_id,
        PDVProduct.category.isnot(None),
        PDVProduct.category != ""
    ).distinct().count()
    
    return schemas.PDVProductStats(
        total_products=total,
        active_products=active,
        fastfood_products=fastfood,
        local_products=local,
        categories_count=categories_count
    )

def create_product(db: Session, product: schemas.PDVProductCreate, terminal_id: int):
    # Verificar se supplier existe e pertence ao terminal
    supplier = None
    if product.supplier_id:
        supplier = db.query(PDVSupplier).filter(
            PDVSupplier.id == product.supplier_id, 
            PDVSupplier.terminal_id == terminal_id
        ).first()
        if not supplier:
            raise HTTPException(status_code=400, detail="Invalid supplier")
    
    # Se supplier for FastFood, marcar produto como FastFood automaticamente
    is_fastfood = product.is_fastfood
    if supplier and supplier.source_type == SourceType.FASTFOOD:
        is_fastfood = True
    
    # Criar produto
    db_product = PDVProduct(
        terminal_id=terminal_id,
        supplier_id=product.supplier_id,
        name=product.name,
        sku=product.sku,
        barcode=product.barcode,
        description=product.description,
        category=product.category,
        cost_price=product.cost_price,
        price=product.price,
        promotional_price=product.promotional_price,
        image=product.image,
        emoji=product.emoji,
        is_fastfood=is_fastfood,
        track_stock=product.track_stock,
        allow_decimal_quantity=product.allow_decimal_quantity
    )
    db.add(db_product)
    db.commit()
    db.refresh(db_product)
    
    # Criar registro de estoque inicial
    initial_qty = product.initial_stock or Decimal("0.00")
    if initial_qty < 0:
        raise HTTPException(status_code=400, detail="Initial stock cannot be negative")
    
    inventory = PDVInventory(
        product_id=db_product.id,
        terminal_id=terminal_id,
        quantity=initial_qty,
        min_quantity=Decimal("0.00"),
        max_quantity=None,
        reserved_quantity=Decimal("0.00"),
        storage_location="balcao"
    )
    db.add(inventory)
    db.commit()
    
    # Se houve estoque inicial, registrar movimento
    if initial_qty > 0:
        movement = PDVStockMovement(
            product_id=db_product.id,
            terminal_id=terminal_id,
            movement_type=MovementType.IN,
            quantity=initial_qty,
            quantity_before=0,
            quantity_after=initial_qty,
            notes="Initial stock",
            created_at=datetime.utcnow()
        )
        db.add(movement)
        
    db.commit()
    db.refresh(db_product)
    return db_product

def update_product(db: Session, product_id: int, updates: schemas.PDVProductUpdate, terminal_id: int):
    product = db.query(PDVProduct).filter(PDVProduct.id == product_id, PDVProduct.terminal_id == terminal_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    update_data = updates.dict(exclude_unset=True)
    supplier_id = update_data.pop("supplier_id", None) if "supplier_id" in update_data else None
    initial_stock = update_data.pop("initial_stock", None) if "initial_stock" in update_data else None

    if "supplier_id" in updates.model_fields_set:
        if supplier_id is None:
            product.supplier_id = None
        else:
            supplier = db.query(PDVSupplier).filter(
                PDVSupplier.id == supplier_id,
                PDVSupplier.terminal_id == terminal_id,
            ).first()
            if not supplier:
                raise HTTPException(status_code=400, detail="Invalid supplier")
            product.supplier_id = supplier.id
            if supplier.source_type == SourceType.FASTFOOD:
                product.is_fastfood = True

    for field, value in update_data.items():
        setattr(product, field, value)

    if initial_stock is not None:
        if initial_stock < 0:
            raise HTTPException(status_code=400, detail="Stock cannot be negative")
        inventory = get_primary_inventory(db, product.id, terminal_id, create_if_missing=True)
        qty_before = inventory.quantity or Decimal("0.00")
        qty_after = initial_stock
        inventory.quantity = qty_after
        inventory.updated_at = datetime.utcnow()

        movement = PDVStockMovement(
            product_id=product.id,
            terminal_id=terminal_id,
            movement_type=MovementType.ADJUSTMENT,
            quantity=qty_after - qty_before,
            quantity_before=qty_before,
            quantity_after=qty_after,
            notes="Stock updated from product form",
        )
        db.add(movement)

    db.commit()
    db.refresh(product)
    return product

def batch_update_fastfood_flag(db: Session, product_ids: List[int], is_fastfood: bool, terminal_id: int):
    """Marcar/desmarcar produtos como FastFood em lote"""
    products = db.query(PDVProduct).filter(
        PDVProduct.id.in_(product_ids),
        PDVProduct.terminal_id == terminal_id
    ).all()
    
    if not products:
        raise HTTPException(status_code=404, detail="No products found")
    
    for product in products:
        product.is_fastfood = is_fastfood
    
    db.commit()
    
    # Refresh para retornar dados atualizados
    for product in products:
        db.refresh(product)
    
    return products

def adjust_stock(db: Session, adjustment: schemas.StockAdjustment, terminal_id: int, user_id: int):
    """
    Ajustar estoque de um produto em um local específico.
    Se o inventário para este local não existir, ele será criado.
    """
    product = db.query(PDVProduct).filter(
        PDVProduct.id == adjustment.product_id, 
        PDVProduct.terminal_id == terminal_id
    ).first()
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
        
    # Encontrar ou criar inventário para o local específico
    inventory = db.query(PDVInventory).filter(
        PDVInventory.product_id == product.id,
        PDVInventory.terminal_id == terminal_id,
        PDVInventory.storage_location == adjustment.storage_location
    ).first()

    if not inventory:
        inventory = PDVInventory(
            product_id=product.id,
            terminal_id=terminal_id,
            storage_location=adjustment.storage_location,
            quantity=0
        )
        db.add(inventory)
        db.commit()
        db.refresh(inventory)
        
    qty_before = inventory.quantity
    
    # Converter MovementTypeEnum (schema) para MovementType (model) se necessário
    # O Pydantic enum pode vir como string ou como enum, então comparamos pelo valor
    movement_type_value = adjustment.movement_type.value if hasattr(adjustment.movement_type, 'value') else str(adjustment.movement_type)
    
    # Calcular nova quantidade
    if movement_type_value == MovementType.IN.value or movement_type_value == "in":
        qty_after = qty_before + adjustment.quantity
    elif movement_type_value == MovementType.OUT.value or movement_type_value == "out":
        qty_after = qty_before - adjustment.quantity
    elif movement_type_value == MovementType.ADJUSTMENT.value or movement_type_value == "adjustment":
        qty_after = adjustment.quantity # Define valor absoluto para o estoque
    else:
        raise HTTPException(status_code=400, detail=f"Invalid movement type for manual adjustment: {movement_type_value}")

    if qty_after < 0:
        raise HTTPException(status_code=400, detail="Stock cannot be negative")
        
    inventory.quantity = qty_after
    inventory.updated_at = datetime.utcnow()
    
    # O valor registrado no campo 'quantity' do movimento deve ser o delta para IN/OUT/SALE
    # Mas para ADJUSTMENT, registramos a diferença necessária para chegar ao valor final.
    movement_qty = adjustment.quantity
    if movement_type_value == MovementType.OUT.value or movement_type_value == "out":
        movement_qty = -adjustment.quantity
    elif movement_type_value == MovementType.ADJUSTMENT.value or movement_type_value == "adjustment":
        movement_qty = qty_after - qty_before
    
    # Converter MovementTypeEnum para MovementType (model) para salvar no banco
    # O enum do modelo espera MovementType, então convertemos o valor
    db_movement_type = MovementType.IN
    if movement_type_value == MovementType.OUT.value or movement_type_value == "out":
        db_movement_type = MovementType.OUT
    elif movement_type_value == MovementType.ADJUSTMENT.value or movement_type_value == "adjustment":
        db_movement_type = MovementType.ADJUSTMENT
    elif movement_type_value == MovementType.SALE.value or movement_type_value == "sale":
        db_movement_type = MovementType.SALE
    elif movement_type_value == MovementType.RETURN.value or movement_type_value == "return":
        db_movement_type = MovementType.RETURN
    elif movement_type_value == MovementType.TRANSFER.value or movement_type_value == "transfer":
        db_movement_type = MovementType.TRANSFER
        
    # Registro de movimento
    movement = PDVStockMovement(
        product_id=product.id,
        terminal_id=terminal_id,
        movement_type=db_movement_type,
        quantity=movement_qty,
        quantity_before=qty_before,
        quantity_after=qty_after,
        notes=f"Local: {adjustment.storage_location}. {adjustment.notes or ''}",
        reference=adjustment.reference,
        created_by=user_id
    )
    
    db.add(movement)
    db.commit()
    
    return movement

def transfer_stock(db: Session, transfer: schemas.StockTransfer, terminal_id: int, user_id: int):
    """
    Transferir estoque entre locais de armazenamento (ex: Armazém -> Balcão).
    """
    product = db.query(PDVProduct).filter(
        PDVProduct.id == transfer.product_id, 
        PDVProduct.terminal_id == terminal_id
    ).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
        
    if transfer.from_location == transfer.to_location:
        raise HTTPException(status_code=400, detail="Source and destination locations must be different")

    # 1. Verificar estoque na origem
    inv_from = db.query(PDVInventory).filter(
        PDVInventory.product_id == product.id,
        PDVInventory.terminal_id == terminal_id,
        PDVInventory.storage_location == transfer.from_location
    ).first()
    
    if not inv_from or inv_from.quantity < transfer.quantity:
        raise HTTPException(status_code=400, detail=f"Insufficient stock in {transfer.from_location}")

    # 2. Garantir que destino existe
    inv_to = db.query(PDVInventory).filter(
        PDVInventory.product_id == product.id,
        PDVInventory.terminal_id == terminal_id,
        PDVInventory.storage_location == transfer.to_location
    ).first()
    
    if not inv_to:
        inv_to = PDVInventory(
            product_id=product.id,
            terminal_id=terminal_id,
            storage_location=transfer.to_location,
            quantity=0
        )
        db.add(inv_to)
        db.commit()
        db.refresh(inv_to)

    # 3. Executar transferência
    qty_before_from = inv_from.quantity
    qty_before_to = inv_to.quantity
    
    inv_from.quantity -= transfer.quantity
    inv_to.quantity += transfer.quantity
    
    # 4. Registrar movimentos de estoque (Saída de um, Entrada no outro)
    # Registramos como um movimento especial de transferência
    movement = PDVStockMovement(
        product_id=product.id,
        terminal_id=terminal_id,
        movement_type=MovementType.TRANSFER,
        quantity=transfer.quantity,
        from_location=transfer.from_location,
        to_location=transfer.to_location,
        notes=transfer.notes or f"Transfer from {transfer.from_location} to {transfer.to_location}",
        created_by=user_id
    )
    db.add(movement)
    db.commit()
    
    return {
        "message": "Transfer successful", 
        "product": product.name,
        "from": {"location": transfer.from_location, "before": float(qty_before_from), "after": float(inv_from.quantity)},
        "to": {"location": transfer.to_location, "before": float(qty_before_to), "after": float(inv_to.quantity)}
    }

# ===================================================================
# Cash Register
# ===================================================================

def get_current_register(db: Session, terminal_id: int, user_id: Optional[int] = None):
    """Obter caixa aberto atualmente."""
    query = db.query(PDVCashRegister).filter(
        PDVCashRegister.terminal_id == terminal_id,
        PDVCashRegister.status == "open"
    )
    if user_id is not None:
        query = query.filter(PDVCashRegister.user_id == user_id)
    return query.order_by(desc(PDVCashRegister.opened_at)).first()

def open_register(db: Session, data: schemas.PDVCashRegisterOpen, terminal_id: int, user_id: int):
    # Verificar se já tem caixa aberto
    existing = get_current_register(db, terminal_id)
    if existing:
        raise HTTPException(status_code=400, detail="There is already an open cash register")
        
    register = PDVCashRegister(
        terminal_id=terminal_id,
        user_id=user_id,
        opening_amount=data.opening_amount,
        notes=data.notes,
        status="open",
        opened_at=datetime.utcnow()
    )
    db.add(register)
    db.commit()
    db.refresh(register)
    return register

def close_register(db: Session, data: schemas.PDVCashRegisterClose, terminal_id: int, user_id: int):
    register = get_current_register(db, terminal_id, user_id=user_id)
    if not register and is_terminal_admin(db, terminal_id, user_id):
        register = get_current_register(db, terminal_id)
    if not register:
        raise HTTPException(status_code=404, detail="No open cash register found")
        
    if register.user_id != user_id:
        # Idealmente apenas o dono ou o próprio operador fecha, mas simplificando
        raise HTTPException(status_code=403, detail="Only the operator who opened the cash register can close it")
        
    # Calcular esperados
    expected = (
        register.opening_amount + 
        register.total_cash + 
        register.total_skywallet +  # SkyWallet conta como valor monetário real
        register.total_card +       # Cartão também
        register.total_mpesa        # Mpesa também
    )
    
    register.closing_amount = data.closing_amount
    register.expected_amount = expected
    register.difference = data.closing_amount - expected
    register.notes = f"{register.notes or ''} | Closing notes: {data.notes or ''}"
    register.status = "closed"
    register.closed_at = datetime.utcnow()
    
    db.commit()
    db.refresh(register)
    return register

def list_cash_registers(
    db: Session,
    terminal_id: int,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    user_id: Optional[int] = None,
):
    query = db.query(PDVCashRegister).filter(PDVCashRegister.terminal_id == terminal_id)
    if start_date:
        query = query.filter(PDVCashRegister.opened_at >= start_date)
    if end_date:
        query = query.filter(PDVCashRegister.opened_at <= end_date)
    if user_id:
        query = query.filter(PDVCashRegister.user_id == user_id)
    return query.order_by(desc(PDVCashRegister.opened_at)).all()

def list_cash_registers(
    db: Session,
    terminal_id: int,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    user_id: Optional[int] = None,
):
    query = db.query(PDVCashRegister).filter(PDVCashRegister.terminal_id == terminal_id)
    if start_date:
        query = query.filter(PDVCashRegister.opened_at >= start_date)
    if end_date:
        query = query.filter(PDVCashRegister.opened_at <= end_date)
    if user_id:
        query = query.filter(PDVCashRegister.user_id == user_id)
    return query.order_by(desc(PDVCashRegister.opened_at)).all()

# ===================================================================
# Sales
# ===================================================================

def create_sale(db: Session, sale_data: schemas.PDVSaleCreate, terminal_id: int, user_id: int):
    # 1. Verificar caixa e terminal
    register = get_current_register(db, terminal_id, user_id=user_id)
    if not register and is_terminal_admin(db, terminal_id, user_id):
        register = get_current_register(db, terminal_id)
    if not register:
        raise HTTPException(status_code=400, detail="Cash register is closed. Please open register first.")
    if register.user_id != user_id:
        raise HTTPException(status_code=403, detail="Use your own open cash register to register sales.")
        
    # 2. Processar Itens
    items_to_add = []
    subtotal = Decimal("0.00")
    
    for item_data in sale_data.items:
        # Apenas PDVProduct (obrigatório)
        product = db.query(PDVProduct).filter(
            PDVProduct.id == item_data.product_id,
            PDVProduct.terminal_id == terminal_id,
            PDVProduct.is_active == True
        ).first()
        
        if not product:
            raise HTTPException(status_code=404, detail=f"Product {item_data.product_id} not found")

        if not product.allow_decimal_quantity and item_data.quantity != item_data.quantity.to_integral_value():
            raise HTTPException(status_code=400, detail=f"Product {product.name} does not allow decimal quantity")

        if product.track_stock:
            inventory = get_primary_inventory(db, product.id, terminal_id, create_if_missing=True)
            available_quantity = inventory.quantity or Decimal("0.00")
            if available_quantity < item_data.quantity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient stock for {product.name}. Available: {available_quantity}",
                )
            
        # Preço (já vem com IVA incluído)
        unit_price = item_data.unit_price if item_data.unit_price is not None else product.price
        
        # Calcular totais do item
        # O preço já inclui IVA, então o total do item é o preço * quantidade
        item_total_raw = unit_price * item_data.quantity
        discount = item_data.discount_amount + (item_total_raw * (item_data.discount_percent / 100))
        item_total = item_total_raw - discount
        
        items_to_add.append({
            "product": product,
            "data": item_data,
            "unit_price": unit_price,
            "item_subtotal": item_total,
            "discount": discount
        })
        
        subtotal += item_total
        
    # 3. Calcular totais da venda
    # Os preços já vêm com IVA incluído
    # Total = subtotal (já com IVA)
    # Subtotal sem IVA = total / 1.16
    # IVA = total - subtotal_sem_iva
    sale_discount = sale_data.discount_amount + (subtotal * (sale_data.discount_percent / 100))
    total_with_discount = subtotal - sale_discount
    
    # Calcular IVA: se o total já inclui IVA, extrair o IVA
    # total = subtotal_sem_iva * 1.16
    # subtotal_sem_iva = total / 1.16
    # iva = total - subtotal_sem_iva
    TAX_RATE = Decimal("0.16")
    subtotal_without_tax = total_with_discount / (Decimal("1.00") + TAX_RATE)
    tax = total_with_discount - subtotal_without_tax
    
    # O total final já está correto (subtotal com desconto)
    total = total_with_discount
    effective_amount_paid = sale_data.amount_paid if sale_data.amount_paid is not None else total
    if sale_data.payment_method == PaymentMethod.CASH and effective_amount_paid < total:
        raise HTTPException(status_code=400, detail="Amount paid cannot be lower than total for cash sales")
    
    # 4. Processar Pagamento (Integração SkyWallet se necessário)
    payment_status = "paid" # Padrão para POS, assumindo pagamento imediato
    
    if sale_data.payment_method == PaymentMethod.SKYWALLET:
        # TODO: Chamar controler skywallet para processar pagamento se tiver user_id ou msisdn
        # Por simplicidade, assume sucesso ou que foi feito externamente e registrado aqui
        pass
        
    # 5. Criar Venda
    # Salvar subtotal sem IVA (já calculado acima como subtotal_without_tax)
    sale = PDVSale(
        terminal_id=terminal_id,
        cash_register_id=register.id,
        customer_id=sale_data.customer_id,
        customer_name=sale_data.customer_name,
        customer_phone=sale_data.customer_phone,
        
        subtotal=subtotal_without_tax,  # Subtotal sem IVA
        discount_amount=sale_discount,
        discount_percent=sale_data.discount_percent,
        tax_amount=tax,  # IVA calculado
        total=total,  # Total com IVA incluído
        
        payment_method=sale_data.payment_method,
        payment_status=payment_status,
        amount_paid=effective_amount_paid,
        change_amount=(effective_amount_paid - total) if effective_amount_paid > total else 0,
        
        sale_type=sale_data.sale_type,
        status="completed",
        
        delivery_address=sale_data.delivery_address,
        delivery_notes=sale_data.delivery_notes,
        notes=sale_data.notes,
        
        created_by=user_id,
        created_at=datetime.utcnow()
    )
    db.add(sale)
    db.commit()
    db.refresh(sale)
    
    # 6. Criar Itens e Atualizar Estoque
    for item_obj in items_to_add:
        product = item_obj["product"]
        data = item_obj["data"]
        
        # Criar SaleItem
        sale_item = PDVSaleItem(
            sale_id=sale.id,
            product_id=product.id,
            product_name=product.name,
            product_sku=product.sku,
            quantity=data.quantity,
            unit_price=item_obj["unit_price"],
            discount_amount=item_obj["discount"],
            discount_percent=data.discount_percent,
            subtotal=item_obj["item_subtotal"],
            notes=data.notes
        )
        db.add(sale_item)
        
        # Atualizar Estoque
        if product.track_stock:
            inventory = get_primary_inventory(db, product.id, terminal_id, create_if_missing=True)
            qty_before = inventory.quantity
            qty_after = qty_before - data.quantity
            if qty_after < 0:
                raise HTTPException(status_code=400, detail=f"Insufficient stock for {product.name}")
            inventory.quantity = qty_after
            
            # Registrar movimento
            movement = PDVStockMovement(
                product_id=product.id,
                terminal_id=terminal_id,
                movement_type=MovementType.SALE,
                quantity=-data.quantity, # Valor negativo
                quantity_before=qty_before,
                quantity_after=qty_after,
                reference=f"Sale #{sale.id}",
                reference_id=sale.id,
                created_by=user_id
            )
            db.add(movement)

    # 7. Atualizar Caixa
    register.total_sales += total
    register.sales_count += 1
    
    if sale.payment_method == PaymentMethod.CASH:
        register.total_cash += total
    elif sale.payment_method == PaymentMethod.CARD:
        register.total_card += total
    elif sale.payment_method == PaymentMethod.SKYWALLET:
        register.total_skywallet += total
    elif sale.payment_method == PaymentMethod.MPESA:
        register.total_mpesa += total
        
    db.commit()
    db.refresh(sale)
    return sale

def get_sales(
    db: Session, 
    terminal_id: int, 
    skip: int = 0, 
    limit: int = 50,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    source_type: Optional[str] = None,
    payment_method: Optional[str] = None,
    sale_type: Optional[str] = None,
    status: Optional[str] = "completed",
    user_id: Optional[int] = None
):
    """
    Lista vendas do terminal.
    Se user_id for fornecido, filtra apenas vendas criadas por esse usuário.
    """
    query = db.query(PDVSale).filter(PDVSale.terminal_id == terminal_id)
    
    # Filtrar por caixa/operador quando informado
    if user_id is not None:
        query = query.filter(PDVSale.created_by == user_id)
    
    if start_date:
        query = query.filter(PDVSale.created_at >= start_date)
    if end_date:
        query = query.filter(PDVSale.created_at <= end_date)
    if source_type:
        if source_type == "local":
            query = query.filter(or_(PDVSale.external_order_type.is_(None), PDVSale.external_order_type == "local"))
        else:
            query = query.filter(PDVSale.external_order_type == source_type)
    if payment_method:
        query = query.filter(PDVSale.payment_method == payment_method)
    if sale_type:
        query = query.filter(PDVSale.sale_type == sale_type)
    if status and status != "all":
        query = query.filter(PDVSale.status == status)
        
    return query.order_by(desc(PDVSale.created_at)).offset(skip).limit(limit).all()

def get_sale_details(db: Session, sale_id: int, terminal_id: int, user_id: Optional[int] = None):
    """
    Obter detalhes de uma venda específica.
    Se user_id for fornecido, só retorna se a venda foi criada por esse usuário.
    """
    query = db.query(PDVSale).filter(PDVSale.id == sale_id, PDVSale.terminal_id == terminal_id)
    
    # Filtrar por caixa/operador quando informado
    if user_id is not None:
        query = query.filter(PDVSale.created_by == user_id)
    
    sale = query.first()
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")
    return sale

# ===================================================================
# Dashboard & Reports
# ===================================================================

def get_dashboard_stats(db: Session, terminal_id: int, user_id: Optional[int] = None):
    """
    Estatísticas do dashboard.
    Se user_id for fornecido, filtra apenas vendas criadas por esse usuário.
    """
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=now.weekday())
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    # Base filter para vendas
    sale_filters = [
        PDVSale.terminal_id == terminal_id,
        PDVSale.status == "completed"
    ]
    
    # Filtrar por caixa/operador quando informado
    if user_id is not None:
        sale_filters.append(PDVSale.created_by == user_id)
    
    # Hoje
    today_sales_data = db.query(
        func.count(PDVSale.id), 
        func.sum(PDVSale.total)
    ).filter(
        *sale_filters,
        PDVSale.created_at >= today_start
    ).first()
    
    today_sales_count = today_sales_data[0] or 0
    today_revenue = today_sales_data[1] or Decimal("0.00")
    
    # Semana
    week_sales_data = db.query(
        func.count(PDVSale.id), 
        func.sum(PDVSale.total)
    ).filter(
        *sale_filters,
        PDVSale.created_at >= week_start
    ).first()
    
    # Mês
    month_sales_data = db.query(
        func.count(PDVSale.id), 
        func.sum(PDVSale.total)
    ).filter(
        *sale_filters,
        PDVSale.created_at >= month_start
    ).first()
    
    # Estoque alertas (sempre mostra para todos, independente de admin)
    low_stock = db.query(func.count(PDVInventory.id)).join(PDVProduct).filter(
        PDVInventory.terminal_id == terminal_id,
        PDVProduct.track_stock == True,
        PDVProduct.is_active == True,
        PDVInventory.quantity <= PDVInventory.min_quantity
    ).scalar() or 0
    
    out_of_stock = db.query(func.count(PDVInventory.id)).join(PDVProduct).filter(
        PDVInventory.terminal_id == terminal_id,
        PDVProduct.track_stock == True,
        PDVProduct.is_active == True,
        PDVInventory.quantity <= 0
    ).scalar() or 0
    
    # Caixa atual (sempre mostra para todos)
    current_register = get_current_register(db, terminal_id)
    
    # Top Products (filtrado por usuário se não for admin)
    top_products_query = db.query(
        PDVSaleItem.product_id,
        PDVSaleItem.product_name,
        func.sum(PDVSaleItem.quantity).label("total_qty"),
        func.sum(PDVSaleItem.subtotal).label("total_revenue")
    ).join(PDVSale).filter(
        PDVSale.terminal_id == terminal_id,
        PDVSale.status == "completed",
        PDVSale.created_at >= month_start
    )
    
    if user_id is not None and not is_terminal_admin(db, terminal_id, user_id):
        top_products_query = top_products_query.filter(PDVSale.created_by == user_id)
    
    top_products_query = top_products_query.group_by(PDVSaleItem.product_id, PDVSaleItem.product_name).order_by(desc("total_qty")).limit(5).all()
    
    top_products = []
    for p in top_products_query:
        top_products.append({
            "product_id": p[0],
            "product_name": p[1],
            "quantity_sold": p[2],
            "revenue": p[3],
            "profit": Decimal("0.00") # WIP: Cost calculation
        })
    
    # Breakdown por pagamento (Mês)
    payment_methods_query = db.query(
        PDVSale.payment_method,
        func.sum(PDVSale.total)
    ).filter(
        PDVSale.terminal_id == terminal_id,
        PDVSale.status == "completed",
        PDVSale.created_at >= month_start
    )
    
    if user_id is not None and not is_terminal_admin(db, terminal_id, user_id):
        payment_methods_query = payment_methods_query.filter(PDVSale.created_by == user_id)
    
    payment_methods_query = payment_methods_query.group_by(PDVSale.payment_method).all()

    payment_breakdown = {}
    total_month_revenue = month_sales_data[1] or Decimal("0.00")
    
    for pm, amt in payment_methods_query:
        percentage = (amt / total_month_revenue * 100) if total_month_revenue > 0 else 0
        payment_breakdown[pm] = {
            "amount": float(amt),
            "percentage": round(float(percentage), 1)
        }

    # Breakdown diário da semana (para o gráfico)
    weekly_breakdown = get_sales_by_day(db, terminal_id, week_start, now, user_id)

    return {
        "today_sales": today_sales_count,
        "today_revenue": today_revenue,
        "today_profit": Decimal("0.00"), 
        "week_sales": week_sales_data[0] or 0,
        "week_revenue": week_sales_data[1] or Decimal("0.00"),
        "month_sales": month_sales_data[0] or 0,
        "month_revenue": month_sales_data[1] or Decimal("0.00"),
        "low_stock_alerts": low_stock,
        "out_of_stock": out_of_stock,
        "current_register_open": current_register is not None,
        "current_register_total": (current_register.total_cash + current_register.opening_amount) if current_register else None,
        "top_products": top_products,
        "payment_breakdown": payment_breakdown,
        "weekly_breakdown": weekly_breakdown
    }

def get_sales_summary(db: Session, terminal_id: int, start_date: datetime, end_date: datetime, user_id: Optional[int] = None):
    """
    Calcular resumo de vendas para um período específico.
    Se user_id for fornecido, filtra apenas vendas criadas por esse usuário.
    """
    # Base filters
    base_filters = [
        PDVSale.terminal_id == terminal_id,
        PDVSale.created_at >= start_date,
        PDVSale.created_at <= end_date
    ]
    
    # Filtrar por caixa/operador quando informado
    if user_id is not None:
        base_filters.append(PDVSale.created_by == user_id)
    
    # Vendas concluídas
    sales_query = db.query(PDVSale).filter(
        *base_filters,
        PDVSale.status == "completed"
    ).all()
    
    # Vendas anuladas
    voided_query = db.query(PDVSale).filter(
        *base_filters,
        PDVSale.status == "voided"
    ).all()
    
    total_sales = len(sales_query)
    total_revenue = sum(s.total for s in sales_query) if sales_query else Decimal("0.00")
    
    # Calcular Custo Total e Lucro (Join com PDVProduct para pegar cost_price atual)
    # Nota: No futuro seria melhor salvar o cost_price no PDVSaleItem no momento da venda.
    items_info_query = db.query(
        func.sum(PDVSaleItem.quantity * PDVProduct.cost_price).label("total_cost"),
        func.sum(PDVSaleItem.quantity).label("total_qty"),
        func.sum(PDVSaleItem.discount_amount).label("total_item_discounts")
    ).join(PDVSale).join(PDVProduct, PDVSaleItem.product_id == PDVProduct.id).filter(
        PDVSale.terminal_id == terminal_id,
        PDVSale.status == "completed",
        PDVSale.created_at >= start_date,
        PDVSale.created_at <= end_date
    )
    
    if user_id is not None:
        items_info_query = items_info_query.filter(PDVSale.created_by == user_id)
    
    items_info = items_info_query.first()
    
    total_cost = items_info.total_cost or Decimal("0.00")
    total_items = items_info.total_qty or 0
    
    # Mapeamento por pagamento
    pm_sums = {
        PaymentMethod.CASH: Decimal("0.00"),
        PaymentMethod.CARD: Decimal("0.00"),
        PaymentMethod.SKYWALLET: Decimal("0.00"),
        PaymentMethod.MPESA: Decimal("0.00"),
        PaymentMethod.MIXED: Decimal("0.00"),
    }
    
    for s in sales_query:
        if s.payment_method in pm_sums:
            pm_sums[s.payment_method] += s.total
            
    return {
        "period_start": start_date,
        "period_end": end_date,
        "total_sales": total_sales,
        "total_revenue": total_revenue,
        "total_cost": total_cost,
        "gross_profit": total_revenue - total_cost,
        "average_sale_value": total_revenue / total_sales if total_sales > 0 else Decimal("0.00"),
        "total_items_sold": int(total_items),
        "total_discounts": sum(s.discount_amount for s in sales_query) + (items_info.total_item_discounts or 0),
        "total_taxes": sum(s.tax_amount for s in sales_query) if sales_query else Decimal("0.00"),
        "cash_sales": pm_sums[PaymentMethod.CASH],
        "card_sales": pm_sums[PaymentMethod.CARD],
        "skywallet_sales": pm_sums[PaymentMethod.SKYWALLET],
        "mpesa_sales": pm_sums[PaymentMethod.MPESA],
        "mixed_sales": pm_sums[PaymentMethod.MIXED],
        "voided_sales": len(voided_query),
        "voided_amount": sum(s.total for s in voided_query) if voided_query else Decimal("0.00")
    }

def get_periodic_report(db: Session, terminal_id: int, period: str, date_str: str, user_id: Optional[int] = None):
    """
    Gera relatório baseado em um período simplificado: 'day', 'month', 'year'
    date_str: '2024-01-21', '2024-01', '2024'
    Se user_id for fornecido e não for admin, filtra apenas vendas criadas por esse usuário.
    """
    try:
        if period == "day":
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            start = dt.replace(hour=0, minute=0, second=0)
            end = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        elif period == "month":
            dt = datetime.strptime(date_str, "%Y-%m")
            start = dt.replace(day=1, hour=0, minute=0, second=0)
            # Próximo mês - 1 dia
            if dt.month == 12:
                next_month = dt.replace(year=dt.year + 1, month=1)
            else:
                next_month = dt.replace(month=dt.month + 1)
            end = next_month - timedelta(microseconds=1)
        elif period == "year":
            dt = datetime.strptime(date_str, "%Y")
            start = dt.replace(month=1, day=1, hour=0, minute=0, second=0)
            end = dt.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999999)
        else:
            raise HTTPException(status_code=400, detail="Invalid period type. Use 'day', 'month' or 'year'.")
            
        return get_sales_summary(db, terminal_id, start, end, user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date format for period {period}. Expected: day=YYYY-MM-DD, month=YYYY-MM, year=YYYY")

def get_detailed_monthly_report(db: Session, terminal_id: int, year: int, month: int, user_id: Optional[int] = None):
    """
    Relatório mensal detalhado com breakdown diário, top produtos, categorias, etc.
    Se user_id for fornecido e não for admin, filtra apenas vendas criadas por esse usuário.
    """
    from calendar import month_name, monthrange
    
    # Calcular período do mês
    start = datetime(year, month, 1, 0, 0, 0)
    last_day = monthrange(year, month)[1]
    end = datetime(year, month, last_day, 23, 59, 59, 999999)
    
    # Resumo geral do mês
    summary = get_sales_summary(db, terminal_id, start, end, user_id)
    
    # Base filters para queries
    base_filters = [
        PDVSale.terminal_id == terminal_id,
        PDVSale.status == "completed",
        PDVSale.created_at >= start,
        PDVSale.created_at <= end
    ]
    
    if user_id is not None and not is_terminal_admin(db, terminal_id, user_id):
        base_filters.append(PDVSale.created_by == user_id)
    
    daily_breakdown = get_sales_by_day(db, terminal_id, start, end, user_id)
    top_products = get_top_products_report(db, terminal_id, start, end, limit=10)
    
    # Top categorias por receita
    categories_query = db.query(
        PDVProduct.category,
        func.sum(PDVSaleItem.subtotal).label("revenue"),
        func.sum(PDVSaleItem.quantity).label("quantity")
    ).join(PDVSaleItem, PDVProduct.id == PDVSaleItem.product_id).join(
        PDVSale, PDVSaleItem.sale_id == PDVSale.id
    ).filter(
        *base_filters,
        PDVProduct.category.isnot(None)
    ).group_by(PDVProduct.category).order_by(desc("revenue")).limit(10).all()
    
    top_categories = [
        {
            "category": cat[0] or "Sem categoria",
            "revenue": float(cat[1] or 0),
            "quantity": int(cat[2] or 0)
        }
        for cat in categories_query
    ]
    
    # Breakdown detalhado por método de pagamento
    payment_breakdown = {}
    pm_query = db.query(
        PDVSale.payment_method,
        func.count(PDVSale.id).label("count"),
        func.sum(PDVSale.total).label("total")
    ).filter(*base_filters).group_by(PDVSale.payment_method).all()
    
    for pm, count, total in pm_query:
        pm_key = pm.value if hasattr(pm, "value") else str(pm)
        payment_breakdown[pm_key] = {
            "count": count,
            "total": float(total or 0),
            "percentage": round(float((total / summary["total_revenue"] * 100) if summary["total_revenue"] > 0 else 0), 1)
        }
    
    # Comparação com mês anterior
    comparison = None
    if month > 1:
        prev_month = month - 1
        prev_year = year
    else:
        prev_month = 12
        prev_year = year - 1
    
    prev_start = datetime(prev_year, prev_month, 1, 0, 0, 0)
    prev_last_day = monthrange(prev_year, prev_month)[1]
    prev_end = datetime(prev_year, prev_month, prev_last_day, 23, 59, 59, 999999)
    prev_summary = get_sales_summary(db, terminal_id, prev_start, prev_end, user_id)
    
    if prev_summary["total_revenue"] > 0:
        revenue_change = ((summary["total_revenue"] - prev_summary["total_revenue"]) / prev_summary["total_revenue"]) * 100
        sales_change = ((summary["total_sales"] - prev_summary["total_sales"]) / prev_summary["total_sales"] * 100) if prev_summary["total_sales"] > 0 else 0
        comparison = {
            "previous_month_revenue": float(prev_summary["total_revenue"]),
            "previous_month_sales": prev_summary["total_sales"],
            "revenue_change_percent": round(float(revenue_change), 1),
            "sales_change_percent": round(float(sales_change), 1)
        }
    
    return {
        "year": year,
        "month": month,
        "month_name": month_name[month],
        "summary": summary,
        "daily_breakdown": daily_breakdown,
        "top_products": top_products,
        "top_categories": top_categories,
        "payment_method_breakdown": payment_breakdown,
        "comparison_previous_month": comparison
    }

def get_detailed_yearly_report(db: Session, terminal_id: int, year: int, user_id: Optional[int] = None):
    """
    Relatório anual detalhado com breakdown mensal, comparação, tendências, etc.
    Se user_id for fornecido e não for admin, filtra apenas vendas criadas por esse usuário.
    """
    from calendar import month_name, monthrange
    
    # Calcular período do ano
    start = datetime(year, 1, 1, 0, 0, 0)
    end = datetime(year, 12, 31, 23, 59, 59, 999999)
    
    # Resumo geral do ano
    summary = get_sales_summary(db, terminal_id, start, end, user_id)
    
    # Base filters
    base_filters = [
        PDVSale.terminal_id == terminal_id,
        PDVSale.status == "completed"
    ]
    
    if user_id is not None and not is_terminal_admin(db, terminal_id, user_id):
        base_filters.append(PDVSale.created_by == user_id)
    
    # Breakdown mensal
    monthly_breakdown = []
    for month in range(1, 13):
        month_start = datetime(year, month, 1, 0, 0, 0)
        last_day = monthrange(year, month)[1]
        month_end = datetime(year, month, last_day, 23, 59, 59, 999999)
        
        month_sales = db.query(
            func.count(PDVSale.id),
            func.sum(PDVSale.total)
        ).filter(
            *base_filters,
            PDVSale.created_at >= month_start,
            PDVSale.created_at <= month_end
        ).first()
        
        monthly_breakdown.append({
            "period": f"{year}-{month:02d}",
            "sales_count": month_sales[0] or 0,
            "total_revenue": month_sales[1] or Decimal("0.00"),
            "average_value": (month_sales[1] / month_sales[0]) if month_sales[0] and month_sales[0] > 0 else Decimal("0.00")
        })
    
    top_products = get_top_products_report(db, terminal_id, start, end, limit=20)
    
    # Top categorias do ano
    categories_query = db.query(
        PDVProduct.category,
        func.sum(PDVSaleItem.subtotal).label("revenue"),
        func.sum(PDVSaleItem.quantity).label("quantity")
    ).join(PDVSaleItem, PDVProduct.id == PDVSaleItem.product_id).join(
        PDVSale, PDVSaleItem.sale_id == PDVSale.id
    ).filter(
        *base_filters,
        PDVSale.created_at >= start,
        PDVSale.created_at <= end,
        PDVProduct.category.isnot(None)
    ).group_by(PDVProduct.category).order_by(desc("revenue")).limit(15).all()
    
    top_categories = [
        {
            "category": cat[0] or "Sem categoria",
            "revenue": float(cat[1] or 0),
            "quantity": int(cat[2] or 0)
        }
        for cat in categories_query
    ]
    
    # Breakdown por método de pagamento
    payment_breakdown = {}
    pm_query = db.query(
        PDVSale.payment_method,
        func.count(PDVSale.id).label("count"),
        func.sum(PDVSale.total).label("total")
    ).filter(
        *base_filters,
        PDVSale.created_at >= start,
        PDVSale.created_at <= end
    ).group_by(PDVSale.payment_method).all()
    
    for pm, count, total in pm_query:
        pm_key = pm.value if hasattr(pm, "value") else str(pm)
        payment_breakdown[pm_key] = {
            "count": count,
            "total": float(total or 0),
            "percentage": round(float((total / summary["total_revenue"] * 100) if summary["total_revenue"] > 0 else 0), 1)
        }
    
    # Tendências sazonais (identificar meses com maior/menor receita)
    if monthly_breakdown:
        revenues = [float(m["total_revenue"]) for m in monthly_breakdown]
        best_month_idx = revenues.index(max(revenues))
        worst_month_idx = revenues.index(min(revenues))
        seasonal_trends = {
            "best_month": month_name[best_month_idx + 1],
            "best_month_revenue": revenues[best_month_idx],
            "worst_month": month_name[worst_month_idx + 1],
            "worst_month_revenue": revenues[worst_month_idx],
            "average_monthly_revenue": sum(revenues) / len(revenues)
        }
    else:
        seasonal_trends = None
    
    # Comparação com ano anterior
    comparison = None
    prev_start = datetime(year - 1, 1, 1, 0, 0, 0)
    prev_end = datetime(year - 1, 12, 31, 23, 59, 59, 999999)
    prev_summary = get_sales_summary(db, terminal_id, prev_start, prev_end, user_id)
    
    if prev_summary["total_revenue"] > 0:
        revenue_change = ((summary["total_revenue"] - prev_summary["total_revenue"]) / prev_summary["total_revenue"]) * 100
        sales_change = ((summary["total_sales"] - prev_summary["total_sales"]) / prev_summary["total_sales"] * 100) if prev_summary["total_sales"] > 0 else 0
        comparison = {
            "previous_year_revenue": float(prev_summary["total_revenue"]),
            "previous_year_sales": prev_summary["total_sales"],
            "revenue_change_percent": round(float(revenue_change), 1),
            "sales_change_percent": round(float(sales_change), 1)
        }
    
    return {
        "year": year,
        "summary": summary,
        "monthly_breakdown": monthly_breakdown,
        "top_products": top_products,
        "top_categories": top_categories,
        "payment_method_breakdown": payment_breakdown,
        "seasonal_trends": seasonal_trends,
        "comparison_previous_year": comparison
    }

def get_top_products_report(db: Session, terminal_id: int, start_date: datetime, end_date: datetime, limit: int = 20, user_id: Optional[int] = None):
    """Relatório de produtos mais vendidos em um período"""
    products_query = db.query(
        PDVSaleItem.product_id,
        PDVSaleItem.product_name,
        PDVProduct.category,
        func.sum(PDVSaleItem.quantity).label("total_qty"),
        func.sum(PDVSaleItem.subtotal).label("total_revenue"),
        func.sum(PDVSaleItem.quantity * PDVProduct.cost_price).label("total_cost")
    ).join(PDVSale, PDVSaleItem.sale_id == PDVSale.id).join(
        PDVProduct, PDVSaleItem.product_id == PDVProduct.id
    ).filter(
        PDVSale.terminal_id == terminal_id,
        PDVSale.status == "completed",
        PDVSale.created_at >= start_date,
        PDVSale.created_at <= end_date
    )

    if user_id:
        products_query = products_query.filter(PDVSale.created_by == user_id)

    products_query = products_query.group_by(
        PDVSaleItem.product_id,
        PDVSaleItem.product_name,
        PDVProduct.category
    ).order_by(desc("total_qty")).limit(limit).all()
    
    top_products = []
    for p in products_query:
        revenue = p[4] or Decimal("0.00")
        cost = p[5] or Decimal("0.00")
        profit = revenue - cost
        top_products.append({
            "product_id": p[0],
            "product_name": p[1],
            "category": p[2],
            "quantity_sold": p[3] or Decimal("0.00"),
            "revenue": revenue,
            "profit": profit
        })
    
    return top_products

def get_sales_by_day(db: Session, terminal_id: int, start_date: datetime, end_date: datetime, user_id: Optional[int] = None):
    """Breakdown de vendas por dia em um período"""
    # Query agrupada por dia
    daily_query = db.query(
        func.date(PDVSale.created_at).label("sale_date"),
        func.count(PDVSale.id).label("sales_count"),
        func.sum(PDVSale.total).label("total_revenue")
    ).filter(
        PDVSale.terminal_id == terminal_id,
        PDVSale.status == "completed",
        PDVSale.created_at >= start_date,
        PDVSale.created_at <= end_date
    )

    if user_id is not None:
        daily_query = daily_query.filter(PDVSale.created_by == user_id)

    daily_query = daily_query.group_by(func.date(PDVSale.created_at)).order_by(func.date(PDVSale.created_at)).all()
    
    daily_breakdown = []
    for day_data in daily_query:
        date_value = day_data[0]
        date_str = date_value.strftime("%Y-%m-%d") if hasattr(date_value, "strftime") else str(date_value)
        count = day_data[1] or 0
        revenue = day_data[2] or Decimal("0.00")
        avg = revenue / count if count > 0 else Decimal("0.00")
        
        daily_breakdown.append({
            "period": date_str,
            "sales_count": count,
            "total_revenue": revenue,
            "average_value": avg
        })
    
    return daily_breakdown

# ===================================================================
# Internal Integration (FastFood -> SkyPDV)
# ===================================================================

def register_fastfood_sale_internal(db: Session, order: Any):
    """Registrar uma venda do FastFood automaticamente no SkyPDV.

    Regras:
    - Esta função deve ser chamada quando o pedido já estiver confirmado/concluído.
    - Deve ser idempotente (não criar venda duplicada para o mesmo pedido).
    - Para pedidos com tab (conta aberta), só registra quando a tab for fechada.
    """
    try:
        # 0. Idempotência: se já existe venda para este pedido, retornar.
        order_id = getattr(order, "id", None)
        if not order_id:
            print("SkyPDV Integrator: Order ID not found")
            return None
            
        existing_sale = db.query(PDVSale).filter(
            PDVSale.external_order_id == order_id,
            PDVSale.external_order_type == "fastfood",
        ).first()
        if existing_sale:
            return existing_sale

        # 1. Carregar restaurante do banco se não estiver carregado
        restaurant_id = getattr(order, "restaurant_id", None)
        if not restaurant_id:
            print(f"SkyPDV Integrator: Restaurant ID not found for order {order_id}")
            return None
            
        restaurant = getattr(order, "restaurant", None)
        if not restaurant:
            restaurant = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
            
        if not restaurant or not getattr(restaurant, "user_id", None):
            print(f"SkyPDV Integrator: Restaurant owner not found for order {order_id}")
            return None

        # 2. Verificar se é pedido com tab (conta aberta) - só registrar quando tab fechar
        tab_id = getattr(order, "tab_id", None)
        if tab_id:
            # Para pedidos com tab, a venda será registrada quando a tab for fechada
            # Não registrar aqui para evitar duplicação
            print(f"SkyPDV Integrator: Order {order_id} has tab {tab_id}, will register when tab closes")
            return None

        terminal = get_or_create_terminal(db, restaurant.user_id)
        if not terminal:
            print(f"SkyPDV Integrator: Terminal could not be created for user {restaurant.user_id}")
            return None

        # 3. Encontrar caixa aberto (se houver)
        register = get_current_register(db, terminal.id)

        # 4. Preparar dados da venda - garantir que items estão carregados
        # Se order.items não estiver carregado, fazer query
        order_items = getattr(order, "items", None)
        if not order_items:
            from models import FastFoodOrderItem
            order_items = db.query(FastFoodOrderItem).filter(
                FastFoodOrderItem.order_id == order_id
            ).all()
            
        sale_items = []
        subtotal = Decimal("0.00")

        for item in order_items or []:
            item_type = (getattr(item, "item_type", "pdv_product") or "pdv_product").lower()
            item_id = getattr(item, "item_id", None)
            quantity = getattr(item, "quantity", 0)
            item_price = getattr(item, "price", None)
            
            if not item_id or not quantity or not item_price:
                print(f"SkyPDV Integrator: Invalid item data for order {order_id}")
                continue
            
            # Apenas PDVProduct (modo atual)
            if item_type == "pdv_product":
                pdv_product = db.query(PDVProduct).filter(
                    PDVProduct.id == item_id,
                    PDVProduct.terminal_id == terminal.id,
                    PDVProduct.is_active == True,
                ).first()
            else:
                # Ignorar tipos antigos (menu_item/drink)
                print(f"SkyPDV Integrator: Legacy item_type {item_type} ignored for order {order_id}")
                continue

            if pdv_product:
                item_total = Decimal(str(item_price)) * Decimal(str(quantity))
                subtotal += item_total

                sale_items.append({
                    "product": pdv_product,
                    "item_id": item_id,
                    "item_type": item_type,
                    "quantity": quantity,
                    "unit_price": Decimal(str(item_price)),
                    "subtotal": item_total,
                })
            else:
                print(f"SkyPDV Integrator: Could not sync/find product for item {item_id} ({item_type})")

        if not sale_items:
            print(f"SkyPDV Integrator: No valid items found to register for order {order_id}.")
            return None

        # 4. Mapear método de pagamento
        pm_raw = (getattr(order, "payment_method", None) or "").strip().lower()
        if pm_raw == "skywallet":
            pdv_payment_method = PaymentMethod.SKYWALLET
        elif pm_raw in ("pos", "card"):
            pdv_payment_method = PaymentMethod.CARD
        elif pm_raw == "mpesa":
            pdv_payment_method = PaymentMethod.MPESA
        else:
            pdv_payment_method = PaymentMethod.CASH

        # 5. Tipo de venda
        order_type_raw = (getattr(order, "order_type", None) or "").strip().lower()
        if getattr(order, "delivery_address", None) or (order_type_raw and order_type_raw != "local"):
            pdv_sale_type = SaleType.DELIVERY
        else:
            pdv_sale_type = SaleType.LOCAL

        # 6. Criar Venda
        sale = PDVSale(
            terminal_id=terminal.id,
            cash_register_id=register.id if register else None,
            customer_id=getattr(order, "user_id", None),
            customer_name=f"Online Customer #{getattr(order, 'user_id', None)}",
            subtotal=subtotal,
            discount_amount=0,
            discount_percent=0,
            tax_amount=0,
            total=subtotal,
            payment_method=pdv_payment_method,
            payment_status="paid",
            amount_paid=subtotal,
            change_amount=0,
            sale_type=pdv_sale_type,
            status="completed",
            delivery_address=getattr(order, "delivery_address", None),
            notes=f"FastFood Order #{getattr(order, 'id', None)}",
            external_order_id=getattr(order, "id", None),
            external_order_type="fastfood",
            created_by=restaurant.user_id,
            created_at=datetime.utcnow(),
        )
        db.add(sale)
        db.commit()
        db.refresh(sale)
        
        # 7. Criar Itens e Baixar Estoque
        for item_data in sale_items:
            product = item_data["product"]
            qty = item_data["quantity"]
            
            sale_item = PDVSaleItem(
                sale_id=sale.id,
                product_id=product.id,
                product_name=product.name,
                product_sku=product.sku,
                quantity=qty,
                unit_price=item_data["unit_price"],
                discount_amount=0,
                discount_percent=0,
                subtotal=item_data["subtotal"],
                notes="Integrated Order"
            )
            db.add(sale_item)
            
            # Atualizar Estoque (se rastreável no PDV) - Dedução do BALCÃO
            if product.track_stock:
                # Buscar inventário especificamente do BALCAO para vendas
                inventory = db.query(PDVInventory).filter(
                    PDVInventory.product_id == product.id,
                    PDVInventory.terminal_id == terminal.id,
                    PDVInventory.storage_location == "balcao"
                ).first()

                if not inventory:
                     # Se não existe no balcao, cria com zero para registrar a saída negativa se permitido
                     inventory = PDVInventory(
                         product_id=product.id, 
                         terminal_id=terminal.id, 
                         storage_location="balcao",
                         quantity=0
                     )
                     db.add(inventory)
                     db.commit() 
                     db.refresh(inventory)
                
                qty_before = inventory.quantity
                qty_after = qty_before - qty
                inventory.quantity = qty_after
                
                movement = PDVStockMovement(
                    product_id=product.id,
                    terminal_id=terminal.id,
                    movement_type=MovementType.SALE,
                    quantity=-qty,
                    quantity_before=qty_before,
                    quantity_after=qty_after,
                    reference=f"Online Sale #{sale.id}",
                    reference_id=sale.id,
                    created_by=restaurant.user_id
                )
                db.add(movement)
        
        # 8. Atualizar Caixa (se existir)
        if register:
            register.total_sales += sale.total
            register.sales_count += 1
            if sale.payment_method == PaymentMethod.SKYWALLET:
                register.total_skywallet += sale.total
            elif sale.payment_method == PaymentMethod.MPESA:
                register.total_mpesa += sale.total
            elif sale.payment_method == PaymentMethod.CARD:
                register.total_card += sale.total
            elif sale.payment_method == PaymentMethod.CASH:
                register.total_cash += sale.total
                 
        db.commit()
        return sale
        
    except Exception as e:
        print(f"Error registering FastFood sale in SkyPDV: {e}")
        # Não propagar erro para não travar o pedido original
        return None


def register_fastfood_tab_sale_internal(
    db: Session,
    tab_id: int,
    payment_method: Optional[str] = None,
):
    """
    Registrar uma venda no SkyPDV quando uma Tab (conta) do FastFood é fechada.

    Regras:
    - Idempotente por tab_id (external_order_type='fastfood_tab', external_order_id=tab_id)
    - Consolida itens de TODOS os pedidos vinculados a esta tab (somando quantidades)
    - Usa preço dos itens gravado em FastFoodOrderItem.price (preço no momento do pedido)
    """
    try:
        existing_sale = db.query(PDVSale).filter(
            PDVSale.external_order_id == tab_id,
            PDVSale.external_order_type == "fastfood_tab",
        ).first()
        if existing_sale:
            return existing_sale

        tab = db.query(FastFoodTab).filter(FastFoodTab.id == tab_id).first()
        if not tab:
            raise HTTPException(status_code=404, detail="FastFood tab not found")

        restaurant = db.query(Restaurant).filter(Restaurant.id == tab.restaurant_id).first()
        if not restaurant or not getattr(restaurant, "user_id", None):
            raise HTTPException(status_code=404, detail="Restaurant owner not found for tab")

        terminal = get_or_create_terminal(db, restaurant.user_id)
        if not terminal:
            raise HTTPException(status_code=404, detail="PDV terminal not found")

        # Caixa aberto (se existir); para integração automática, não bloqueia se estiver fechado
        register = get_current_register(db, terminal.id)

        # Buscar pedidos da tab
        orders = db.query(FastFoodOrder).filter(FastFoodOrder.tab_id == tab_id).all()
        if not orders:
            # Nada para registrar (tab vazia)
            return None

        # Buscar itens de todos os pedidos
        order_ids = [o.id for o in orders if o and getattr(o, "id", None)]
        if not order_ids:
            return None

        items = db.query(FastFoodOrderItem).filter(FastFoodOrderItem.order_id.in_(order_ids)).all()
        if not items:
            return None

        # Consolidar por (item_type, item_id, unit_price)
        aggregated: dict[tuple[str, int, Decimal], int] = {}
        for it in items:
            item_type = (getattr(it, "item_type", "menu_item") or "menu_item").lower()
            item_id = getattr(it, "item_id", None)
            qty = int(getattr(it, "quantity", 0) or 0)
            unit_price_raw = getattr(it, "price", None)
            if not item_id or qty <= 0 or unit_price_raw is None:
                continue
            unit_price = Decimal(str(unit_price_raw))
            key = (item_type, int(item_id), unit_price)
            aggregated[key] = aggregated.get(key, 0) + qty

        if not aggregated:
            return None

        sale_items = []
        subtotal = Decimal("0.00")
        for (item_type, item_id, unit_price), qty in aggregated.items():
            pdv_product = get_or_create_pdv_product_from_fastfood(db, terminal.id, item_id, item_type)
            if not pdv_product:
                continue
            line_total = unit_price * Decimal(qty)
            subtotal += line_total
            sale_items.append(
                {
                    "product": pdv_product,
                    "item_id": item_id,
                    "item_type": item_type,
                    "quantity": qty,
                    "unit_price": unit_price,
                    "subtotal": line_total,
                }
            )

        if not sale_items:
            return None

        # Mapear método de pagamento (prioriza parâmetro; fallback: tab/payment_method do último pedido)
        pm_raw = (payment_method or getattr(orders[-1], "payment_method", None) or "").strip().lower()
        if pm_raw == "skywallet":
            pdv_payment_method = PaymentMethod.SKYWALLET
        elif pm_raw in ("pos", "card"):
            pdv_payment_method = PaymentMethod.CARD
        elif pm_raw == "mpesa":
            pdv_payment_method = PaymentMethod.MPESA
        else:
            pdv_payment_method = PaymentMethod.CASH

        sale = PDVSale(
            terminal_id=terminal.id,
            cash_register_id=register.id if register else None,
            customer_id=None,
            customer_name=f"FastFood Tab #{tab_id}",
            subtotal=subtotal,
            discount_amount=0,
            discount_percent=0,
            tax_amount=0,
            total=subtotal,
            payment_method=pdv_payment_method,
            payment_status="paid",
            amount_paid=subtotal,
            change_amount=0,
            sale_type=SaleType.LOCAL,
            status="completed",
            delivery_address=None,
            notes=f"FastFood Tab #{tab_id}",
            external_order_id=tab_id,
            external_order_type="fastfood_tab",
            created_by=restaurant.user_id,
            created_at=datetime.utcnow(),
        )
        db.add(sale)
        db.commit()
        db.refresh(sale)

        # Criar itens e baixar estoque PDV (se rastreável)
        for item_data in sale_items:
            product = item_data["product"]
            qty = int(item_data["quantity"])

            sale_item = PDVSaleItem(
                sale_id=sale.id,
                product_id=product.id,
                product_name=product.name,
                product_sku=product.sku,
                quantity=qty,
                unit_price=item_data["unit_price"],
                discount_amount=0,
                discount_percent=0,
                subtotal=item_data["subtotal"],
                notes="Integrated Tab"
            )
            db.add(sale_item)

            if product.track_stock:
                inventory = db.query(PDVInventory).filter(
                    PDVInventory.product_id == product.id,
                    PDVInventory.terminal_id == terminal.id,
                    PDVInventory.storage_location == "balcao"
                ).first()
                if not inventory:
                    inventory = PDVInventory(
                        product_id=product.id,
                        terminal_id=terminal.id,
                        storage_location="balcao",
                        quantity=0
                    )
                    db.add(inventory)
                    db.commit()
                    db.refresh(inventory)

                qty_before = inventory.quantity
                qty_after = qty_before - qty
                inventory.quantity = qty_after

                movement = PDVStockMovement(
                    product_id=product.id,
                    terminal_id=terminal.id,
                    movement_type=MovementType.SALE,
                    quantity=-qty,
                    quantity_before=qty_before,
                    quantity_after=qty_after,
                    reference=f"FastFood Tab Sale #{sale.id}",
                    reference_id=sale.id,
                    created_by=restaurant.user_id
                )
                db.add(movement)

        if register:
            register.total_sales += sale.total
            register.sales_count += 1
            if sale.payment_method == PaymentMethod.SKYWALLET:
                register.total_skywallet += sale.total
            elif sale.payment_method == PaymentMethod.MPESA:
                register.total_mpesa += sale.total
            elif sale.payment_method == PaymentMethod.CARD:
                register.total_card += sale.total
            elif sale.payment_method == PaymentMethod.CASH:
                register.total_cash += sale.total

        db.commit()
        return sale

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error registering FastFood tab sale in SkyPDV: {e}")
        return None

def cancel_fastfood_sale_internal(db: Session, order_id: int):
    """
    Cancelar venda integrada quando o pedido FastFood é cancelado.
    """
    try:
        # Buscar venda pelo ID externo
        sale = db.query(PDVSale).filter(
            PDVSale.external_order_id == order_id,
            PDVSale.external_order_type == "fastfood"
        ).first()
        
        if not sale or sale.status == "voided":
            return
            
        sale.status = "voided"
        
        # Estornar estoque
        sale_items = db.query(PDVSaleItem).filter(PDVSaleItem.sale_id == sale.id).all()
        for item in sale_items:
            product = db.query(PDVProduct).filter(PDVProduct.id == item.product_id).first()
            if product and product.track_stock and product.inventory:
                inventory = product.inventory
                qty_before = inventory.quantity
                qty_after = qty_before + item.quantity
                inventory.quantity = qty_after
                
                movement = PDVStockMovement(
                    product_id=product.id,
                    terminal_id=sale.terminal_id,
                    movement_type=MovementType.RETURN,
                    quantity=item.quantity,
                    quantity_before=qty_before,
                    quantity_after=qty_after,
                    reference=f"Voided Sale #{sale.id}",
                    reference_id=sale.id
                )
                db.add(movement)
                
        # Estornar valores do caixa
        if sale.cash_register_id:
            register = db.query(PDVCashRegister).filter(PDVCashRegister.id == sale.cash_register_id).first()
            if register and register.status == "open":
                register.total_sales -= sale.total
                register.sales_count -= 1
                register.refunds_count += 1
                register.total_refunds += sale.total
                
                if sale.payment_method == PaymentMethod.SKYWALLET:
                    register.total_skywallet -= sale.total
        
        db.commit()
        return True
    
    except Exception as e:
        print(f"Error cancelling FastFood sale in SkyPDV: {e}")
        return False

def void_sale(db: Session, sale_id: int, terminal_id: int, user_id: int):
    """
    Anula uma venda, estornando stock e atualizando o caixa.
    """
    sale = db.query(PDVSale).filter(PDVSale.id == sale_id, PDVSale.terminal_id == terminal_id).first()
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")

    # Caixas normais só podem anular suas próprias vendas
    if not is_terminal_admin(db, terminal_id, user_id):
        if sale.created_by != user_id:
            raise HTTPException(status_code=403, detail="Not allowed to void this sale")
        
    if sale.status == "voided":
        raise HTTPException(status_code=400, detail="Sale is already voided")

    sale.status = "voided"
    sale.payment_status = "voided"
    
    # Estornar stock
    for item in sale.items:
        product = item.product
        if product and product.track_stock and product.inventory:
            inventory = product.inventory
            qty_before = inventory.quantity
            qty_after = qty_before + item.quantity
            inventory.quantity = qty_after
            
            # Registrar movimento
            movement = PDVStockMovement(
                product_id=product.id,
                terminal_id=terminal_id,
                movement_type=MovementType.RETURN,
                quantity=item.quantity,
                quantity_before=qty_before,
                quantity_after=qty_after,
                reference=f"Voided Sale #{sale.id}",
                reference_id=sale.id,
                created_by=user_id
            )
            db.add(movement)

    # Estornar valores do caixa
    if sale.cash_register_id:
        register = sale.cash_register
        if register and register.status == "open":
            register.total_sales -= sale.total
            register.sales_count -= 1
            register.refunds_count += 1
            register.total_refunds += sale.total
            
            if sale.payment_method == PaymentMethod.CASH:
                register.total_cash -= sale.total
            elif sale.payment_method == PaymentMethod.CARD:
                register.total_card -= sale.total
            elif sale.payment_method == PaymentMethod.SKYWALLET:
                register.total_skywallet -= sale.total
            elif sale.payment_method == PaymentMethod.MPESA:
                register.total_mpesa -= sale.total

    db.commit()
    db.refresh(sale)
    return sale

def get_stock_movements(db: Session, terminal_id: int, product_id: Optional[int] = None, skip: int = 0, limit: int = 100):
    """Retorna o histórico de movimentações de stock"""
    query = db.query(PDVStockMovement).filter(PDVStockMovement.terminal_id == terminal_id)
    if product_id:
        query = query.filter(PDVStockMovement.product_id == product_id)
    return query.order_by(desc(PDVStockMovement.created_at)).offset(skip).limit(limit).all()

def get_inventory_report(db: Session, terminal_id: int):
    """Gera um relatório detalhado do inventário atual"""
    inventory_items = db.query(PDVInventory).filter(PDVInventory.terminal_id == terminal_id).all()
    
    total_products = len(inventory_items)
    total_value = Decimal("0.00")
    total_retail_value = Decimal("0.00")
    low_stock_count = 0
    out_of_stock_count = 0
    
    report_items = []
    for inv in inventory_items:
        product = inv.product
        if not product or not product.is_active:
            continue
            
        total_value += product.cost_price * inv.quantity
        total_retail_value += product.price * inv.quantity
        
        if inv.quantity <= 0:
            out_of_stock_count += 1
        elif inv.quantity <= inv.min_quantity:
            low_stock_count += 1
            
        # Adicionar info do produto para o schema PDVInventory
        inv.product_name = product.name
        inv.product_sku = product.sku
        report_items.append(inv)
                
    return {
        "total_products": total_products,
        "total_value": total_value,
        "total_retail_value": total_retail_value,
        "low_stock_count": low_stock_count,
        "out_of_stock_count": out_of_stock_count,
        "products": report_items
    }

def sync_supplier(db: Session, supplier_id: int, terminal_id: int):
    """Força sincronização de um fornecedor externo"""
    supplier = db.query(PDVSupplier).filter(PDVSupplier.id == supplier_id, PDVSupplier.terminal_id == terminal_id).first()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    
    supplier.last_sync_at = datetime.utcnow()
    db.commit()
    db.refresh(supplier)
    return supplier

def delete_product(db: Session, product_id: int, terminal_id: int):
    """Desativa um produto do PDV"""
    product = db.query(PDVProduct).filter(PDVProduct.id == product_id, PDVProduct.terminal_id == terminal_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
        
    product.is_active = False
    db.commit()
    return {"message": "Product deactivated successfully"}

# ===================================================================
# Categories Management
# ===================================================================

def get_categories_list(db: Session, terminal_id: int):
    """Listar categorias do terminal + categorias globais"""
    # Categorias do próprio terminal
    own_categories = db.query(PDVCategory).filter(
        PDVCategory.terminal_id == terminal_id,
        PDVCategory.is_active == True
    ).all()
    
    # Categorias globais (compartilhadas)
    global_categories = db.query(PDVCategory).filter(
        PDVCategory.is_global == True,
        PDVCategory.is_active == True
    ).all()
    
    # Combinar e remover duplicatas
    all_categories = {cat.id: cat for cat in own_categories + global_categories}
    return list(all_categories.values())

def create_category(db: Session, category: schemas.PDVCategoryCreate, terminal_id: int, user_id: int, is_global: bool = False):
    """Criar nova categoria (pessoal ou global)"""
    db_category = PDVCategory(
        terminal_id=None if is_global else terminal_id,
        created_by=user_id,
        is_global=is_global,
        **category.dict()
    )
    db.add(db_category)
    db.commit()
    db.refresh(db_category)
    return db_category

def adopt_category(db: Session, category_id: int, terminal_id: int, user_id: int):
    """Adotar uma categoria global para o terminal"""
    global_cat = db.query(PDVCategory).filter(
        PDVCategory.id == category_id,
        PDVCategory.is_global == True
    ).first()
    
    if not global_cat:
        raise HTTPException(status_code=404, detail="Global category not found")
    
    # Criar cópia para o terminal
    new_cat = PDVCategory(
        terminal_id=terminal_id,
        created_by=user_id,
        name=global_cat.name,
        description=global_cat.description,
        icon=global_cat.icon,
        color=global_cat.color,
        is_global=False
    )
    db.add(new_cat)
    db.commit()
    db.refresh(new_cat)
    return new_cat

def update_category(db: Session, category_id: int, updates: schemas.PDVCategoryUpdate, terminal_id: int):
    """Atualizar categoria"""
    category = db.query(PDVCategory).filter(PDVCategory.id == category_id, PDVCategory.terminal_id == terminal_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
        
    update_data = updates.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(category, field, value)
        
    db.commit()
    db.refresh(category)
    return category

def delete_category(db: Session, category_id: int, terminal_id: int):
    """Desativar categoria"""
    category = db.query(PDVCategory).filter(PDVCategory.id == category_id, PDVCategory.terminal_id == terminal_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    category.is_active = False
    db.commit()
    return {"message": "Category deactivated successfully"}

# ===================================================================
# Payment Methods Management
# ===================================================================

def get_payment_methods_list(db: Session, terminal_id: int):
    """Listar métodos do terminal + métodos globais"""
    # Métodos do próprio terminal
    own_methods = db.query(PDVPaymentMethod).filter(
        PDVPaymentMethod.terminal_id == terminal_id,
        PDVPaymentMethod.is_active == True
    ).all()
    
    # Métodos globais (compartilhados)
    global_methods = db.query(PDVPaymentMethod).filter(
        PDVPaymentMethod.is_global == True,
        PDVPaymentMethod.is_active == True
    ).all()
    
    # Combinar e remover duplicatas
    all_methods = {m.id: m for m in own_methods + global_methods}
    return list(all_methods.values())

def create_payment_method(db: Session, method: schemas.PDVPaymentMethodCreate, terminal_id: int, user_id: int, is_global: bool = False):
    """Criar novo método de pagamento (pessoal ou global)"""
    db_method = PDVPaymentMethod(
        terminal_id=None if is_global else terminal_id,
        created_by=user_id,
        is_global=is_global,
        **method.dict()
    )
    db.add(db_method)
    db.commit()
    db.refresh(db_method)
    return db_method

def adopt_payment_method(db: Session, method_id: int, terminal_id: int, user_id: int):
    """Adotar um método de pagamento global para o terminal"""
    global_method = db.query(PDVPaymentMethod).filter(
        PDVPaymentMethod.id == method_id,
        PDVPaymentMethod.is_global == True
    ).first()
    
    if not global_method:
        raise HTTPException(status_code=404, detail="Global payment method not found")
    
    # Criar cópia para o terminal
    new_method = PDVPaymentMethod(
        terminal_id=terminal_id,
        created_by=user_id,
        name=global_method.name,
        description=global_method.description,
        icon=global_method.icon,
        is_global=False
    )
    db.add(new_method)
    db.commit()
    db.refresh(new_method)
    return new_method

def update_payment_method(db: Session, method_id: int, updates: schemas.PDVPaymentMethodUpdate, terminal_id: int):
    """Atualizar método de pagamento"""
    method = db.query(PDVPaymentMethod).filter(PDVPaymentMethod.id == method_id, PDVPaymentMethod.terminal_id == terminal_id).first()
    if not method:
        raise HTTPException(status_code=404, detail="Payment method not found")
        
    update_data = updates.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(method, field, value)
        
    db.commit()
    db.refresh(method)
    return method

def delete_payment_method(db: Session, method_id: int, terminal_id: int):
    """Desativar método de pagamento"""
    method = db.query(PDVPaymentMethod).filter(PDVPaymentMethod.id == method_id, PDVPaymentMethod.terminal_id == terminal_id).first()
    if not method:
        raise HTTPException(status_code=404, detail="Payment method not found")
    
    method.is_active = False
    db.commit()
    return {"message": "Payment method deactivated successfully"}

# ===================================================================
# Finance Management
# ===================================================================

def get_expense_categories_list(db: Session, terminal_id: int):
    own_categories = db.query(PDVExpenseCategory).filter(
        PDVExpenseCategory.terminal_id == terminal_id,
        PDVExpenseCategory.is_active == True
    ).all()
    if not own_categories:
        terminal = db.query(PDVTerminal).filter(PDVTerminal.id == terminal_id).first()
        owner_id = terminal.user_id if terminal else None
        default_expense_categories = [
            {"name": "Renda da Loja", "code": "aluguel", "icon": "store", "color": "#ef4444"},
            {"name": "Salário", "code": "salario", "icon": "users", "color": "#f59e0b"},
            {"name": "Internet", "code": "internet", "icon": "wifi", "color": "#3b82f6"},
            {"name": "Combustível", "code": "combustivel", "icon": "truck", "color": "#10b981"},
            {"name": "Fornecedor", "code": "fornecedor", "icon": "package", "color": "#8b5cf6"},
            {"name": "Energia", "code": "energia", "icon": "bolt", "color": "#06b6d4"},
            {"name": "Água", "code": "agua", "icon": "droplet", "color": "#0ea5e9"},
            {"name": "Outras Despesas", "code": "outros", "icon": "receipt", "color": "#64748b"},
        ]
        for item in default_expense_categories:
            db.add(
                PDVExpenseCategory(
                    terminal_id=terminal_id,
                    created_by=owner_id,
                    name=item["name"],
                    code=item["code"],
                    icon=item["icon"],
                    color=item["color"],
                    is_global=False,
                    is_active=True,
                )
            )
        db.commit()
        own_categories = db.query(PDVExpenseCategory).filter(
            PDVExpenseCategory.terminal_id == terminal_id,
            PDVExpenseCategory.is_active == True
        ).all()
    global_categories = db.query(PDVExpenseCategory).filter(
        PDVExpenseCategory.is_global == True,
        PDVExpenseCategory.is_active == True
    ).all()
    all_categories = {c.id: c for c in own_categories + global_categories}
    return list(all_categories.values())


def create_expense_category(db: Session, category: schemas.PDVExpenseCategoryCreate, terminal_id: int, user_id: int, is_global: bool = False):
    db_category = PDVExpenseCategory(
        terminal_id=None if is_global else terminal_id,
        created_by=user_id,
        is_global=is_global,
        **category.dict()
    )
    db.add(db_category)
    db.commit()
    db.refresh(db_category)
    return db_category


def update_expense_category(db: Session, category_id: int, updates: schemas.PDVExpenseCategoryUpdate, terminal_id: int):
    category = db.query(PDVExpenseCategory).filter(
        PDVExpenseCategory.id == category_id,
        PDVExpenseCategory.terminal_id == terminal_id
    ).first()
    if not category:
        raise HTTPException(status_code=404, detail="Expense category not found")

    update_data = updates.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(category, field, value)

    db.commit()
    db.refresh(category)
    return category


def delete_expense_category(db: Session, category_id: int, terminal_id: int):
    category = db.query(PDVExpenseCategory).filter(
        PDVExpenseCategory.id == category_id,
        PDVExpenseCategory.terminal_id == terminal_id
    ).first()
    if not category:
        raise HTTPException(status_code=404, detail="Expense category not found")

    category.is_active = False
    db.commit()
    return {"message": "Expense category deactivated successfully"}


def get_expenses(
    db: Session,
    terminal_id: int,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    category_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 100,
):
    query = db.query(PDVExpense).filter(
        PDVExpense.terminal_id == terminal_id,
        PDVExpense.is_active == True,
    )
    if start_date:
        query = query.filter(PDVExpense.expense_date >= start_date)
    if end_date:
        query = query.filter(PDVExpense.expense_date <= end_date)
    if category_id:
        query = query.filter(PDVExpense.category_id == category_id)

    items = query.order_by(desc(PDVExpense.expense_date)).offset(skip).limit(limit).all()
    result = []
    for item in items:
        item.category_name = item.category.name if item.category else None
        item.category_code = item.category.code if item.category else None
        result.append(item)
    return result


def create_expense(db: Session, expense: schemas.PDVExpenseCreate, terminal_id: int, user_id: int):
    if expense.category_id:
        category = db.query(PDVExpenseCategory).filter(
            PDVExpenseCategory.id == expense.category_id,
            or_(PDVExpenseCategory.terminal_id == terminal_id, PDVExpenseCategory.is_global == True),
            PDVExpenseCategory.is_active == True,
        ).first()
        if not category:
            raise HTTPException(status_code=404, detail="Expense category not found")

    db_expense = PDVExpense(
        terminal_id=terminal_id,
        created_by=user_id,
        **expense.dict()
    )
    db.add(db_expense)
    db.commit()
    db.refresh(db_expense)
    db_expense.category_name = db_expense.category.name if db_expense.category else None
    db_expense.category_code = db_expense.category.code if db_expense.category else None
    return db_expense


def update_expense(db: Session, expense_id: int, updates: schemas.PDVExpenseUpdate, terminal_id: int):
    expense = db.query(PDVExpense).filter(
        PDVExpense.id == expense_id,
        PDVExpense.terminal_id == terminal_id
    ).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")

    update_data = updates.dict(exclude_unset=True)
    if "category_id" in update_data and update_data["category_id"]:
        category = db.query(PDVExpenseCategory).filter(
            PDVExpenseCategory.id == update_data["category_id"],
            or_(PDVExpenseCategory.terminal_id == terminal_id, PDVExpenseCategory.is_global == True),
            PDVExpenseCategory.is_active == True,
        ).first()
        if not category:
            raise HTTPException(status_code=404, detail="Expense category not found")

    for field, value in update_data.items():
        setattr(expense, field, value)

    db.commit()
    db.refresh(expense)
    expense.category_name = expense.category.name if expense.category else None
    expense.category_code = expense.category.code if expense.category else None
    return expense


def delete_expense(db: Session, expense_id: int, terminal_id: int):
    expense = db.query(PDVExpense).filter(
        PDVExpense.id == expense_id,
        PDVExpense.terminal_id == terminal_id
    ).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")

    expense.is_active = False
    db.commit()
    return {"message": "Expense deactivated successfully"}


def get_financial_summary(
    db: Session,
    terminal_id: int,
    start_date: datetime,
    end_date: datetime,
    user_id: Optional[int] = None,
):
    sales_summary = get_sales_summary(db, terminal_id, start_date, end_date, user_id)
    expenses = db.query(PDVExpense).filter(
        PDVExpense.terminal_id == terminal_id,
        PDVExpense.is_active == True,
        PDVExpense.expense_date >= start_date,
        PDVExpense.expense_date <= end_date,
    ).all()

    total_expenses = sum((expense.amount for expense in expenses), Decimal("0.00"))
    breakdown_map = {}
    for expense in expenses:
        key = expense.category_id or 0
        if key not in breakdown_map:
            breakdown_map[key] = {
                "category_id": expense.category_id,
                "category_name": expense.category.name if expense.category else "Sem categoria",
                "category_code": expense.category.code if expense.category else None,
                "total_amount": Decimal("0.00"),
            }
        breakdown_map[key]["total_amount"] += expense.amount

    return {
        "period_start": start_date,
        "period_end": end_date,
        "gross_revenue": sales_summary["total_revenue"],
        "gross_profit": sales_summary["gross_profit"],
        "total_expenses": total_expenses,
        "net_profit": sales_summary["gross_profit"] - total_expenses,
        "sales_count": sales_summary["total_sales"],
        "expenses_count": len(expenses),
        "expense_breakdown": list(breakdown_map.values()),
    }

# ===================================================================
# Image Upload
# ===================================================================

async def upload_pdv_product_image(file: UploadFile) -> str:
    """
    Upload a product image for SkyPDV to the specific SkyPDV folder.
    """
    if not file:
        raise HTTPException(status_code=400, detail="No file sent")
    from controllers.storage_manager import SKYPDV_PRODUCT_FOLDER, StorageManager

    try:
        storage = StorageManager()
        return storage.upload_file(file, destination_folder=SKYPDV_PRODUCT_FOLDER)
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Failed to upload image: {str(e)}")
