from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File, Request
import os
from fastapi.responses import StreamingResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from typing import List, Optional
from datetime import datetime, timedelta
from pydantic import BaseModel
import io

from database import get_db
from auth import get_current_user
from models import User, PDVStockMovement, MovementType, PDVSale, PDVSaleItem, PDVProduct, PDVInventory, PDVSupplier, PDVTerminal, PDVCashRegister, FastFoodRestaurant, PDVPaymentMethod
import schemas
from controllers import controller
from controllers.skywallet_gateway import SkyWalletGatewayClient
import openpyxl
from openpyxl.utils import get_column_letter
from whatsapp_service import send_whatsapp_file, send_whatsapp_text

# Criar router principal
router = APIRouter(
    prefix="/skypdv",
    tags=["skypdv"]
)


@router.get("/config")
def get_config():
    """Retorna configurações simples do SkyPDV (flags habilitadas via env)."""
    activate = os.getenv("SKYPDV_ACTIVATE_CHARGING", "false").strip().lower() in ("1", "true", "yes")
    return {"activate_charging": activate}


def _mt_val(mt):
    """Normalize a movement_type value to a plain string value."""
    try:
        return mt.value
    except Exception:
        return str(mt)


def _mt_in(mt, *targets):
    v = _mt_val(mt)
    for t in targets:
        tv = t.value if hasattr(t, "value") else str(t)
        if v == tv:
            return True
    return False


def _fmt_product_quantity(value, is_weighted: bool = False) -> str:
    """Format a quantity with the product's actual selling unit."""
    try:
        from decimal import Decimal
        amount = Decimal(str(value or 0))
        formatted = format(amount, "f").rstrip("0").rstrip(".") or "0"
        return f"{formatted} Kg" if is_weighted else formatted
    except Exception:
        return "0 Kg" if is_weighted else "0"

# ===================================================================
# Terminal Endpoints
# ===================================================================

@router.get("/terminal", response_model=schemas.PDVTerminal)
def get_my_terminal(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Obter o terminal PDV do usuário atual (requer setup)"""
    return controller.get_terminal_required(db, current_user.id)


@router.post("/terminal/setup", response_model=schemas.PDVTerminal)
def setup_my_terminal(
    terminal_data: schemas.PDVTerminalCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Fazer setup do PDV (criar terminal manualmente para lojas/farmácias)"""
    return controller.create_terminal_for_user(db, current_user.id, terminal_data)

@router.post("/terminal", response_model=schemas.PDVTerminal)
def create_my_terminal(
    terminal_data: schemas.PDVTerminalCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Alias compatível para criação de terminal (POST /terminal)."""
    return controller.create_terminal_for_user(db, current_user.id, terminal_data)

@router.put("/terminal", response_model=schemas.PDVTerminal)
def update_my_terminal(
    terminal_update: schemas.PDVTerminalUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Atualizar configurações do terminal"""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.update_terminal(db, terminal.id, terminal_update, current_user.id)

# ===================================================================
# Subscription & SkyWallet Endpoints
# ===================================================================

class PayAdvanceRequest(BaseModel):
    months: int

@router.get("/skywallet/balance")
async def get_skywallet_balance(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Obter saldo do SkyWallet do usuário"""
    terminal = controller.get_terminal_required(db, current_user.id)
    wallet_client = SkyWalletGatewayClient()
    user_details = {
        "central_user_id": str(current_user.central_user_id),
        "email": current_user.email,
        "full_name": current_user.name,
        "username": current_user.username
    }
    return await wallet_client.get_balance(str(current_user.central_user_id), user_details)

@router.post("/terminal/subscription/pay")
async def pay_subscription(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Pagar assinatura mensal (1 mês)"""
    terminal = controller.get_terminal_required(db, current_user.id)
    wallet_client = SkyWalletGatewayClient()
    user_details = {
        "central_user_id": str(current_user.central_user_id),
        "email": current_user.email,
        "full_name": current_user.name,
        "username": current_user.username
    }
    
    # Get balance first
    balance_data = await wallet_client.get_balance(str(current_user.central_user_id), user_details)
    main_balance = float(balance_data.get("balance", {}).get("main_balance", 0))
    
    if main_balance < 1200:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Saldo insuficiente. Por favor, recarregue sua SkyWallet.",
            headers={"X-Need-Deposit": "true"}
        )
    
    # Charge user
    reference = f"skypdv-subscription-{terminal.id}-{datetime.utcnow().isoformat()}"
    await wallet_client.charge(user_details, 1200.0, reference, {"product_code": "skypdv"})
    
    # Update terminal subscription
    if terminal.next_billing_date and terminal.next_billing_date > datetime.utcnow():
        terminal.next_billing_date = terminal.next_billing_date + timedelta(days=30)
    else:
        terminal.next_billing_date = datetime.utcnow() + timedelta(days=30)
    terminal.subscription_status = "active"
    terminal.grace_period_ends_at = None
    db.commit()
    db.refresh(terminal)
    
    return terminal

@router.post("/terminal/subscription/pay-advance")
async def pay_advance_subscription(
    request: PayAdvanceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Pagar assinatura antecipada (múltiplos meses)"""
    months = max(1, min(12, request.months))  # Limit 1-12 months
    total_amount = 1200 * months
    
    terminal = controller.get_terminal_required(db, current_user.id)
    wallet_client = SkyWalletGatewayClient()
    user_details = {
        "central_user_id": str(current_user.central_user_id),
        "email": current_user.email,
        "full_name": current_user.name,
        "username": current_user.username
    }
    
    # Get balance first
    balance_data = await wallet_client.get_balance(str(current_user.central_user_id), user_details)
    main_balance = float(balance_data.get("balance", {}).get("main_balance", 0))
    
    if main_balance < total_amount:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Saldo insuficiente. Necessário: {total_amount} MT. Por favor, recarregue sua SkyWallet.",
            headers={"X-Need-Deposit": "true"}
        )
    
    # Charge user
    reference = f"skypdv-subscription-advance-{terminal.id}-{datetime.utcnow().isoformat()}"
    await wallet_client.charge(user_details, total_amount, reference, {"product_code": "skypdv", "months": months})
    
    # Update terminal subscription
    if terminal.next_billing_date and terminal.next_billing_date > datetime.utcnow():
        terminal.next_billing_date = terminal.next_billing_date + timedelta(days=30 * months)
    else:
        terminal.next_billing_date = datetime.utcnow() + timedelta(days=30 * months)
    terminal.subscription_status = "active"
    terminal.grace_period_ends_at = None
    db.commit()
    db.refresh(terminal)
    
    return terminal


@router.post("/terminal/subscription/pay-all")
async def pay_all_terminals(
    restaurant_id: Optional[int] = Query(None, description="ID do restaurante (FastFood) para pagar todos os terminais associados"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Permite que o admin pague a assinatura de TODOS os terminais associados a um restaurante FastFood.

    - Se `restaurant_id` for fornecido, valida que o `current_user` é dono do restaurante.
    - Cobra o valor total (1200 MT por terminal) da SkyWallet do usuário e atualiza os terminais.
    """
    wallet_client = SkyWalletGatewayClient()

    # Descobrir terminais a pagar
    if restaurant_id is None:
        # Sem restaurant_id: operar apenas no terminal do usuário atual
        terminal = controller.get_terminal_required(db, current_user.id)
        terminal_ids = [terminal.id]
    else:
        # Verificar restaurante e permissões
        restaurant = db.query(FastFoodRestaurant).filter(FastFoodRestaurant.id == restaurant_id).first()
        if not restaurant:
            raise HTTPException(status_code=404, detail="Restaurant not found")
        if restaurant.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Only restaurant owner can perform bulk payment")

        suppliers = db.query(PDVSupplier).filter(
            PDVSupplier.source_type == "fastfood",
            PDVSupplier.external_id == restaurant_id
        ).all()
        terminal_ids = list({s.terminal_id for s in suppliers})

    if not terminal_ids:
        raise HTTPException(status_code=400, detail="No terminals found to pay")

    total_amount = 1200.0 * len(terminal_ids)

    user_details = {
        "central_user_id": str(current_user.central_user_id),
        "email": current_user.email,
        "full_name": current_user.name,
        "username": current_user.username
    }

    balance_data = await wallet_client.get_balance(str(current_user.central_user_id), user_details)
    main_balance = float(balance_data.get("balance", {}).get("main_balance", 0))
    if main_balance < total_amount:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Saldo insuficiente. Necessário: {total_amount} MT para pagar {len(terminal_ids)} terminais.",
            headers={"X-Need-Deposit": "true"}
        )

    reference = f"skypdv-subscription-pay-all-{current_user.id}-{datetime.utcnow().isoformat()}"
    await wallet_client.charge(user_details, total_amount, reference, {"product_code": "skypdv", "terminals": terminal_ids})

    # Atualizar terminais
    from models import PDVTerminal
    updated = []
    for tid in terminal_ids:
        t = db.query(PDVTerminal).filter(PDVTerminal.id == tid).first()
        if not t:
            continue
        if t.next_billing_date and t.next_billing_date > datetime.utcnow():
            t.next_billing_date = t.next_billing_date + timedelta(days=30)
        else:
            t.next_billing_date = datetime.utcnow() + timedelta(days=30)
        t.subscription_status = "active"
        t.grace_period_ends_at = None
        db.add(t)
        updated.append(t)

    db.commit()
    # Refresh objects
    for t in updated:
        db.refresh(t)

    return updated

# DEPRECATED: Deposits are now handled exclusively through SkyWallet
# Users must go to https://skywallet.bluesparkmz.com to deposit funds
# @router.post("/skywallet/deposit")
# async def deposit_skywallet(...):
#     """Endpoint deprecated - use SkyWallet for deposits"""

# ===================================================================
# Terminal Users Management - Gestão de usuários do terminal
# ===================================================================

@router.get("/terminal/users", response_model=List[schemas.PDVTerminalUser])
def list_terminal_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Lista todos os usuários associados ao terminal"""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.get_terminal_users(db, terminal.id, current_user.id)


@router.post("/terminal/users", response_model=schemas.PDVTerminalUser)
def add_terminal_user(
    user_data: schemas.PDVTerminalUserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Adiciona um usuário ao terminal pelo email"""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.add_terminal_user(db, terminal.id, user_data.email, user_data, current_user.id)


@router.put("/terminal/users/{terminal_user_id}", response_model=schemas.PDVTerminalUser)
def update_terminal_user(
    terminal_user_id: int,
    user_update: schemas.PDVTerminalUserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Atualiza permissões de um usuário do terminal"""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.update_terminal_user(db, terminal.id, terminal_user_id, user_update, current_user.id)


@router.delete("/terminal/users/{terminal_user_id}")
def remove_terminal_user(
    terminal_user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Remove um usuário do terminal"""
    terminal = controller.get_terminal_required(db, current_user.id)
    controller.remove_terminal_user(db, terminal.id, terminal_user_id, current_user.id)
    return {"message": "User removed from terminal successfully"}

# ===================================================================
# Suppliers Endpoints
# ===================================================================

@router.get("/suppliers", response_model=List[schemas.PDVSupplier])
def list_suppliers(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Listar fornecedores conectados ao terminal"""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.get_suppliers(db, terminal.id)

@router.post("/suppliers", response_model=schemas.PDVSupplier)
def add_supplier(
    supplier: schemas.PDVSupplierCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Adicionar novo fornecedor manual"""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.create_supplier(db, supplier, terminal.id)

@router.post("/suppliers/connect/fastfood", response_model=schemas.PDVSupplier)
def connect_fastfood(
    data: schemas.ConnectFastFoodRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Conectar um restaurante FastFood como fornecedor"""
    terminal = controller.get_or_create_terminal(db, current_user.id)
    return controller.connect_fastfood_restaurant(db, terminal.id, data.restaurant_id, data.sync_products)

@router.post("/suppliers/{supplier_id}/sync", response_model=schemas.PDVSupplier)
def force_sync_supplier(
    supplier_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Sincronizar manualmente produtos de um fornecedor externo (FastFood)"""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.sync_supplier(db, supplier_id, terminal.id)

@router.get("/suppliers/{supplier_id}/products", response_model=List[schemas.PDVProduct])
def list_supplier_products(
    supplier_id: int,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Listar produtos de um fornecedor específico (ex: FastFood)"""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.get_products(
        db,
        terminal.id,
        supplier_id=supplier_id,
        limit=limit,
        skip=skip
    )

@router.put("/suppliers/{supplier_id}", response_model=schemas.PDVSupplier)
def update_supplier(
    supplier_id: int,
    updates: schemas.PDVSupplierUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Atualizar fornecedor"""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.update_supplier(db, supplier_id, updates, terminal.id)

@router.delete("/suppliers/{supplier_id}")
def delete_supplier(
    supplier_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Desativar fornecedor"""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.delete_supplier(db, supplier_id, terminal.id)

# ===================================================================
# Products & Inventory Endpoints
# ===================================================================

@router.get("/products", response_model=List[schemas.PDVProduct])
def list_products(
    search: Optional[str] = None,
    category: Optional[str] = None,
    source_type: Optional[schemas.SourceTypeEnum] = None,
    is_fastfood: Optional[bool] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Listar produtos com filtros (incluindo is_fastfood para FastFood)"""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.get_products(
        db, terminal.id, 
        search=search, 
        category=category, 
        source_type=source_type,
        is_fastfood=is_fastfood,
        limit=limit,
        skip=skip
    )


@router.get("/products/category-summary/today", response_model=schemas.PDVCategorySalesSummary)
def get_category_sales_summary_today(
    category: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.get_category_sales_summary_today(db, terminal.id, category)


@router.get("/products/category-report", response_model=schemas.PDVCategorySalesReport)
def get_category_sales_report(
    category: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    if not controller.is_terminal_admin(db, terminal.id, current_user.id):
        user_id = current_user.id
    return controller.get_category_sales_report(
        db,
        terminal.id,
        category,
        start_date=start_date,
        end_date=end_date,
        user_id=user_id,
    )

@router.post("/products", response_model=schemas.PDVProduct)
def create_product(
    product: schemas.PDVProductCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Criar novo produto no PDV"""
    terminal = controller.get_terminal_required(db, current_user.id)
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_manage_products")
    return controller.create_product(db, product, terminal.id)


@router.get("/products/catalog", response_model=List[schemas.PDVProduct])
def list_shared_products(
    search: Optional[str] = None,
    category: Optional[str] = None,
    business_type: Optional[str] = Query(None, pattern="^(loja|restaurante)$"),
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.get_shared_products(
        db,
        terminal.id,
        search=search,
        category=category,
        business_type=business_type,
        limit=limit,
        skip=skip,
    )


@router.post("/products/adopt", response_model=schemas.PDVProduct)
def adopt_shared_product(
    payload: schemas.PDVProductAdopt,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_manage_products")
    return controller.adopt_shared_product(
        db,
        terminal.id,
        payload.source_product_id,
        price=payload.price,
        cost_price=payload.cost_price,
        initial_stock=payload.initial_stock,
    )

@router.post("/products/upload-image")
async def upload_product_image(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Upload an image for a PDV product.
    Returns the URL of the uploaded image.
    """
    # Verify user has access to a terminal (is a valid PDV user)
    terminal = controller.get_terminal_required(db, current_user.id)
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_manage_products")
    
    url = await controller.upload_pdv_product_image(file)
    return {"url": url}


@router.get("/products/csv-template")
def download_csv_template():
    """
    Retorna o modelo de arquivo CSV para importação em massa de produtos.
    """
    content = "nome,quantidade,preco\nExemplo Produto 1,10,150.00\nExemplo Produto 2,50,25.50\n"
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=modelo_produtos_skypdv.csv"}
    )


@router.post("/products/import-csv")
def bulk_import_products_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Importar produtos em massa via arquivo CSV (nome, quantidade, preco, categoria).
    Produtos com o mesmo nome que já existam no sistema serão ignorados.
    """
    terminal = controller.get_terminal_required(db, current_user.id)
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_manage_products")
    return controller.bulk_import_products_csv(db, file, terminal.id)


@router.post("/invoice-assets/upload")
async def upload_invoice_asset(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Upload logo or stamp image for invoice settings.
    """
    terminal = controller.get_terminal_required(db, current_user.id)
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_sell")

    url = await controller.upload_pdv_invoice_asset(file)
    return {"url": url}

@router.post("/products/search", response_model=List[schemas.PDVProduct])
def search_products(
    search: schemas.PDVProductSearch,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Busca avançada de produtos com múltiplos filtros (incluindo is_fastfood e supplier_id)"""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.get_products(
        db,
        terminal.id,
        search=search.query,
        category=search.category,
        source_type=search.source_type.value if search.source_type else None,
        is_fastfood=search.is_fastfood,
        supplier_id=search.supplier_id,
        limit=search.limit,
        skip=search.skip
    )

@router.get("/products/stats", response_model=schemas.PDVProductStats)
def get_product_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Estatísticas de produtos (total, ativos, FastFood, locais, categorias)"""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.get_product_stats(db, terminal.id)

@router.patch("/products/{product_id}", response_model=schemas.PDVProduct)
@router.put("/products/{product_id}", response_model=schemas.PDVProduct, include_in_schema=False)
def update_product(
    product_id: int,
    updates: schemas.PDVProductUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Atualizar produto"""
    terminal = controller.get_terminal_required(db, current_user.id)
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_manage_products")
    return controller.update_product(db, product_id, updates, terminal.id)

@router.put("/products/batch/fastfood", response_model=List[schemas.PDVProduct])
def batch_update_fastfood_flag(
    batch: schemas.PDVProductBatchFastFood,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Marcar/desmarcar produtos como FastFood em lote"""
    terminal = controller.get_terminal_required(db, current_user.id)
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_manage_products")
    return controller.batch_update_fastfood_flag(db, batch.product_ids, batch.is_fastfood, terminal.id)

@router.delete("/products/{product_id}")
def delete_product(
    product_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Desativar um produto (Marcar como inativo)"""
    terminal = controller.get_terminal_required(db, current_user.id)
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_manage_products")
    return controller.delete_product(db, product_id, terminal.id)

@router.get("/products/{product_id}/movements", response_model=List[schemas.PDVStockMovement])
def list_stock_movements(
    product_id: int,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Histórico de movimentações de stock de um produto"""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.get_stock_movements(db, terminal.id, product_id, skip, limit)

@router.get("/inventory/movements", response_model=List[schemas.PDVStockMovement])
def list_inventory_movements(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Histórico global de movimentações de stock do terminal"""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.get_stock_movements(db, terminal.id, None, skip, limit)

@router.post("/inventory/adjustment", response_model=schemas.PDVStockMovement)
def adjust_inventory(
    adjustment: schemas.StockAdjustment,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Ajustar estoque manual (entrada/saída/balanço)"""
    terminal = controller.get_terminal_required(db, current_user.id)
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_manage_stock")
    return controller.adjust_stock(db, adjustment, terminal.id, current_user.id)

@router.post("/inventory/transfer", response_model=schemas.PDVStockMovement)
def transfer_inventory(
    transfer: schemas.StockTransfer,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Transferir estoque entre localizações"""
    terminal = controller.get_terminal_required(db, current_user.id)
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_manage_stock")
    return controller.transfer_stock(db, transfer, terminal.id, current_user.id)

@router.get("/inventory", response_model=schemas.InventoryReport)
def get_inventory_report(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Relatório detalhado de inventário e stock baixo"""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.get_inventory_report(db, terminal.id)

@router.put("/inventory/{product_id}", response_model=schemas.PDVInventory)
def update_inventory_config(
    product_id: int,
    updates: schemas.PDVInventoryUpdate,
    storage_location: str = "balcao",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Atualizar configuracoes do inventario por local"""
    terminal = controller.get_terminal_required(db, current_user.id)
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_manage_stock")
    return controller.update_inventory_settings(db, product_id, terminal.id, storage_location, updates)

# ===================================================================
# Cash Register Endpoints
# ===================================================================

@router.get("/cash-register/current", response_model=Optional[schemas.PDVCashRegister])
def get_current_register(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Obter sessão do caixa atual"""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.get_current_register(db, terminal.id, current_user.id)

@router.post("/cash-register/open", response_model=schemas.PDVCashRegister)
def open_register(
    data: schemas.PDVCashRegisterOpen,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Abrir o caixa"""
    terminal = controller.get_terminal_required(db, current_user.id)
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_open_cash_register")
    return controller.open_register(db, data, terminal.id, current_user.id)

@router.post("/cash-register/close", response_model=schemas.PDVCashRegister)
def close_register(
    data: schemas.PDVCashRegisterClose,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Fechar o caixa"""
    terminal = controller.get_terminal_required(db, current_user.id)
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_open_cash_register")
    return controller.close_register(db, data, terminal.id, current_user.id)


@router.get("/cash-register/{register_id}/report.pdf")
def download_cash_register_report(
    register_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Baixar o relatório PDF de um caixa fechado do terminal atual."""
    terminal = controller.get_terminal_required(db, current_user.id)
    register = (
        db.query(PDVCashRegister)
        .filter(PDVCashRegister.id == register_id, PDVCashRegister.terminal_id == terminal.id)
        .first()
    )
    if not register:
        raise HTTPException(status_code=404, detail="Caixa não encontrado.")
    if register.status != "closed":
        raise HTTPException(status_code=400, detail="O relatório só está disponível após o fechamento do caixa.")

    pdf_bytes = controller.generate_cash_register_report_pdf(db, register)
    closed_at = register.closed_at or datetime.utcnow()
    filename = f"fechamento_caixa_{register.id}_{closed_at.strftime('%Y%m%d_%H%M')}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


@router.get("/cash-register/history", response_model=List[schemas.PDVCashRegister])
def list_cash_registers(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Histórico de caixas (apenas admins podem filtrar por usuário)."""
    terminal = controller.get_terminal_required(db, current_user.id)
    # Se não for admin, força usar apenas o próprio user_id
    if not controller.is_terminal_admin(db, terminal.id, current_user.id):
        user_id = current_user.id
    return controller.list_cash_registers(db, terminal.id, start_date, end_date, user_id)

# ===================================================================
# Outflows (saidas de produto e dinheiro)
# ===================================================================

@router.get("/outflows", response_model=List[schemas.PDVOutflow])
def list_outflows(
    outflow_type: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.list_outflows(db, terminal.id, outflow_type, start_date, end_date, skip, limit)


@router.get("/outflows/summary", response_model=schemas.PDVOutflowSummary)
def get_outflow_summary(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.get_outflow_summary(db, terminal.id, start_date, end_date)


@router.post("/outflows", response_model=schemas.PDVOutflow)
def create_outflow(
    data: schemas.PDVOutflowCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.create_outflow(db, data, terminal.id, current_user.id)


@router.post("/outflows/{outflow_id}/cancel")
def cancel_outflow(
    outflow_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.cancel_outflow(db, outflow_id, terminal.id, current_user.id)


@router.get("/outflows/report.pdf")
def get_outflows_report_pdf(
    outflow_type: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Relatório de saídas: uma tabela com os registos e totais em texto.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, HRFlowable
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from xml.sax.saxutils import escape as _esc
    from models import PDVOutflow as PDVOutflowModel, User as UserModel

    terminal = controller.get_terminal_required(db, current_user.id)
    currency = terminal.currency or "MT"

    # Default: se não informado, hoje
    if not start_date:
        start_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    if not end_date:
        end_date = datetime.utcnow()

    # Query
    q = db.query(PDVOutflowModel).filter(
        PDVOutflowModel.terminal_id == terminal.id,
        PDVOutflowModel.is_active == True,
        PDVOutflowModel.created_at >= start_date,
        PDVOutflowModel.created_at <= end_date,
    )
    if outflow_type:
        q = q.filter(PDVOutflowModel.outflow_type == outflow_type)
    outflows = q.order_by(PDVOutflowModel.created_at.desc()).all()

    # Operadores
    user_ids = list({o.created_by for o in outflows if o.created_by})
    users_map: dict = {}
    if user_ids:
        users = db.query(UserModel).filter(UserModel.id.in_(user_ids)).all()
        users_map = {u.id: (u.name or u.username or str(u.id)) for u in users}

    REASON_LABELS = {
        "consumo_interno": "Consumo interno",
        "cafetaria": "Cafetaria",
        "cozinha": "Cozinha",
        "perda": "Perda / Avaria",
        "despesa_diaria": "Despesa diária",
        "outro": "Outro",
    }

    def _fmt_dt(dt) -> str:
        local_dt = controller.to_mozambique_datetime(dt)
        return local_dt.strftime("%d/%m/%Y %H:%M") if local_dt else ""

    def _fmt_date(dt) -> str:
        return dt.strftime("%d/%m/%Y") if dt else ""

    def _fmt_money(v) -> str:
        try:
            return f"{float(v):,.2f}"
        except Exception:
            return "0.00"

    def _fmt_qty(v) -> str:
        try:
            val = float(v)
            return str(int(val)) if val == int(val) else f"{val:.2f}"
        except Exception:
            return "0"

    def _is_prod_outflow(o):
        v = getattr(o.outflow_type, "value", str(o.outflow_type)).lower()
        return "product" in v

    # Separar produtos e despesas de caixa
    prod_outflows = [o for o in outflows if _is_prod_outflow(o)]
    cash_outflows = [o for o in outflows if not _is_prod_outflow(o)]
    total_qty = sum(float(o.quantity or 0) for o in prod_outflows)
    total_cash = sum(float(o.amount or 0) for o in cash_outflows)
    total_product_sale_value = sum(float(o.quantity or 0) * float((o.product.price if o.product else 0) or 0) for o in prod_outflows)

    # Build PDF
    C_BLACK  = colors.HexColor("#111111")
    C_TEXT   = colors.HexColor("#222222")
    C_MUTED  = colors.HexColor("#555555")
    C_LINE   = colors.HexColor("#CCCCCC")
    C_HEADER = colors.HexColor("#111111")
    C_ROW    = colors.HexColor("#F5F5F5")
    C_WHITE  = colors.white

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=14*mm, rightMargin=14*mm,
        topMargin=14*mm, bottomMargin=14*mm,
    )
    styles = getSampleStyleSheet()
    ST_TITLE = ParagraphStyle("OutTitle", parent=styles["Title"], fontSize=16, leading=20, textColor=C_BLACK, spaceAfter=2, alignment=TA_LEFT)
    ST_SUB   = ParagraphStyle("OutSub", parent=styles["Normal"], fontSize=8.5, leading=12, textColor=C_MUTED)
    ST_H2    = ParagraphStyle("OutH2", parent=styles["Heading2"], fontSize=11, leading=14, textColor=C_BLACK, spaceBefore=14, spaceAfter=6, fontName="Helvetica-Bold")
    ST_BODY  = ParagraphStyle("OutBody", parent=styles["Normal"], fontSize=9, leading=13, textColor=C_TEXT, spaceBefore=1, spaceAfter=1)
    ST_BODY_B= ParagraphStyle("OutBodyB", parent=styles["Normal"], fontSize=9.5, leading=13.5, textColor=C_BLACK, spaceBefore=2, spaceAfter=2, fontName="Helvetica-Bold")
    ST_CELL  = ParagraphStyle("OutCell", parent=styles["Normal"], fontSize=8, leading=10.5, textColor=C_TEXT)
    ST_FOOT  = ParagraphStyle("OutFoot", parent=styles["Normal"], fontSize=7.5, textColor=C_MUTED, alignment=TA_CENTER)

    w_usable = doc.width
    story = []

    type_label = {
        "product": "Produtos",
        "cash": "Caixa",
    }.get(outflow_type or "", "Todas")

    story.append(Paragraph(_esc(terminal.name or "SkyPDV"), ST_TITLE))
    if terminal.address:
        story.append(Paragraph(_esc(terminal.address), ST_SUB))
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=1, color=C_BLACK, spaceAfter=8))
    story.append(Paragraph("Relatório de saídas", ST_H2))
    story.append(Paragraph(
        f"Período: {_fmt_date(start_date)} até {_fmt_date(end_date)}    ·    Tipo: {type_label}    ·    Emitido: {_fmt_dt(datetime.utcnow())}",
        ST_SUB,
    ))

    out_headers = ["Data", "Tipo", "Descrição", "Motivo", "Destino", "Qtd", "Valor potencial", "Operador"]
    out_cw_raw = [70, 52, 120, 70, 70, 32, 62, 70]
    scale_out = w_usable / sum(out_cw_raw)
    out_cw = [w * scale_out for w in out_cw_raw]
    out_rows = [out_headers]
    for o in outflows:
        is_prod = _is_prod_outflow(o)
        tipo = "Produto" if is_prod else "Caixa"
        if is_prod and o.product and o.product.name:
            desc = o.product.name
        else:
            desc = o.title or "—"
        reason = REASON_LABELS.get(o.reason or "", o.reason or "—")
        dest = o.destination or "—"
        op = users_map.get(o.created_by, "—") if o.created_by else "—"
        qty = _fmt_product_quantity(o.quantity or 0, bool(o.product and o.product.allow_decimal_quantity)) if is_prod else "—"
        product_sale_value = float(o.quantity or 0) * float((o.product.price if o.product else 0) or 0)
        valor = f"{_fmt_money(o.amount or 0)} {currency}" if not is_prod else f"{_fmt_money(product_sale_value)} {currency}"
        out_rows.append([
            _fmt_dt(o.created_at),
            tipo,
            Paragraph(_esc(str(desc)), ST_CELL),
            Paragraph(_esc(str(reason)), ST_CELL),
            Paragraph(_esc(str(dest)), ST_CELL),
            qty,
            valor,
            Paragraph(_esc(str(op)), ST_CELL),
        ])
    if len(out_rows) == 1:
        out_rows.append(["Sem saídas no período", "—", "—", "—", "—", "—", "—", "—"])

    out_tbl = Table(out_rows, colWidths=out_cw, repeatRows=1)
    out_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), C_HEADER),
        ("TEXTCOLOR",  (0, 0), (-1, 0), C_WHITE),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, -1), 8),
        ("TEXTCOLOR",  (0, 1), (-1, -1), C_TEXT),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C_WHITE, C_ROW]),
        ("ALIGN",      (5, 0), (6, -1), "RIGHT"),
        ("ALIGN",      (0, 0), (0, -1), "LEFT"),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("BOX",       (0, 0), (-1, -1), 0.4, C_LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, C_LINE),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, C_BLACK),
    ]))
    story.append(out_tbl)
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"Total de saídas em dinheiro: {_fmt_money(total_cash)} {currency}", ST_BODY_B))
    story.append(Paragraph(f"Quantidade total retirada: {_fmt_qty(total_qty)} (consulte Kg/unidade em cada produto)", ST_BODY))
    story.append(Paragraph(f"Valor potencial de venda dos produtos retirados: {_fmt_money(total_product_sale_value)} {currency}", ST_BODY_B))
    story.append(Paragraph(f"Registos: {len(outflows)}", ST_BODY))

    story.append(Spacer(1, 14))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_LINE, spaceAfter=6))
    story.append(Paragraph(
        f"SkyPDV — Relatório de saídas gerado em {_fmt_dt(datetime.utcnow())}  |  Terminal: {_esc(terminal.name or '—')}",
        ST_FOOT,
    ))

    doc.build(story)
    buffer.seek(0)

    period_str = f"{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}"
    filename = f"saidas-{period_str}.pdf"
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ===================================================================
# Sales Endpoints
# ===================================================================

@router.post("/sales", response_model=schemas.PDVSale)
def create_sale(
    sale: schemas.PDVSaleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Registrar nova venda"""
    terminal = controller.get_terminal_required(db, current_user.id)
    # Verificar bloqueio de venda apenas se SKYPDV_ACTIVATE_CHARGING=true
    enforce_charging = os.getenv("SKYPDV_ACTIVATE_CHARGING", "false").strip().lower() in ("1", "true", "yes")
    if enforce_charging and terminal.subscription_status == "suspended":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Terminal suspenso devido a falta de pagamento da assinatura. Por favor, efetue o pagamento para reativar."
        )
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_sell")
    return controller.create_sale(db, sale, terminal.id, current_user.id)

@router.get("/sales", response_model=List[schemas.PDVSale])
def list_sales(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    source_type: Optional[str] = None,
    payment_method: Optional[str] = None,
    sale_type: Optional[str] = None,
    status: Optional[str] = "completed",
    skip: int = 0,
    limit: int = 50,
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Listar histórico de vendas com filtros.
    - Caixas veem apenas suas próprias vendas
    - Admins veem todas as vendas do terminal
    - Se user_id for fornecido e usuário for admin, filtra por esse caixa específico
    Permite visualizar:
    - Por período de data
    - Por origem da venda (mantido por compatibilidade)
    - Por tipo de venda (sale_type: local, delivery, online)
    - Por método de pagamento
    """
    terminal = controller.get_terminal_required(db, current_user.id)
    # Date-only query parameters are parsed at 00:00. Treat the end date as
    # inclusive so /sales?end_date=YYYY-MM-DD includes the selected day.
    if end_date and end_date.hour == 0 and end_date.minute == 0 and end_date.second == 0:
        end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    if controller.is_terminal_admin(db, terminal.id, current_user.id):
        filter_user_id = user_id
    else:
        filter_user_id = current_user.id
    return controller.get_sales(
        db, terminal.id, 
        skip=skip, 
        limit=limit,
        start_date=start_date,
        end_date=end_date,
        source_type=source_type,
        payment_method=payment_method,
        sale_type=sale_type,
        status=status,
        user_id=filter_user_id
    )

# ===================================================================
# Invoice Endpoints (usam o mesmo modelo de venda)
# ===================================================================

@router.post("/invoices", response_model=schemas.PDVSale)
def create_invoice(
    sale: schemas.PDVSaleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    # Verificar bloqueio de venda apenas se SKYPDV_ACTIVATE_CHARGING=true
    enforce_charging = os.getenv("SKYPDV_ACTIVATE_CHARGING", "false").strip().lower() in ("1", "true", "yes")
    if enforce_charging and terminal.subscription_status == "suspended":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Terminal suspenso devido a falta de pagamento da assinatura. Por favor, efetue o pagamento para reativar."
        )
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_sell")
    return controller.create_invoice(db, sale, terminal.id, current_user.id)

@router.get("/invoices", response_model=List[schemas.PDVSale])
def list_invoices(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    payment_status: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    if controller.is_terminal_admin(db, terminal.id, current_user.id):
        filter_user_id = user_id
    else:
        filter_user_id = current_user.id
    status = None  # retorna todas para ver pendentes e pagas
    sales = controller.get_sales(
        db, terminal.id,
        skip=skip,
        limit=limit,
        start_date=start_date,
        end_date=end_date,
        status=status,
        user_id=filter_user_id
    )
    # Faturas sao vendas que carregam invoice_meta no campo notes.
    # Isso mantem separado o fluxo de venda comum/recibo termico.
    invoice_sales = [sale for sale in sales if controller._extract_invoice_meta(sale.notes)]

    if payment_status:
        invoice_sales = [s for s in invoice_sales if getattr(s, "payment_status", None) == payment_status]
    return invoice_sales


@router.get("/invoice-customers", response_model=List[schemas.PDVInvoiceCustomer])
def list_invoice_customers(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_sell")
    return controller.list_invoice_customers(db, terminal.id)


@router.post("/invoice-customers", response_model=schemas.PDVInvoiceCustomer)
def create_invoice_customer(
    payload: schemas.PDVInvoiceCustomerCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_sell")
    return controller.create_invoice_customer(db, payload, terminal.id, current_user.id)


@router.put("/invoice-customers/{customer_id}", response_model=schemas.PDVInvoiceCustomer)
def update_invoice_customer(
    customer_id: int,
    payload: schemas.PDVInvoiceCustomerUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_sell")
    return controller.update_invoice_customer(db, customer_id, payload, terminal.id)


@router.delete("/invoice-customers/{customer_id}")
def delete_invoice_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_sell")
    controller.delete_invoice_customer(db, customer_id, terminal.id)
    return {"message": "Invoice customer deleted"}


@router.post("/invoices/{invoice_id}/pay", response_model=schemas.PDVSale)
def pay_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_sell")
    sale = db.query(PDVSale).filter(
        PDVSale.id == invoice_id,
        PDVSale.terminal_id == terminal.id
    ).first()
    if not sale:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if not controller._extract_invoice_meta(sale.notes):
        raise HTTPException(status_code=400, detail="This sale is not an invoice")
    return controller.mark_invoice_paid(db, invoice_id, terminal.id, current_user.id)


@router.post("/invoices/{invoice_id}/generate-receipt", response_model=schemas.PDVSale)
def generate_invoice_receipt(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    controller.require_terminal_permission(db, terminal.id, current_user.id, "can_sell")
    return controller.mark_invoice_receipt_generated(db, invoice_id, terminal.id, current_user.id)

@router.get("/invoices/{invoice_id}/pdf")
def get_invoice_pdf(
    invoice_id: int,
    phone: Optional[str] = None,
    document_type: str = Query("invoice", pattern="^(invoice|receipt)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    # Pode ver a própria venda ou, se admin, qualquer uma
    sale = db.query(PDVSale).filter(
        PDVSale.id == invoice_id,
        PDVSale.terminal_id == terminal.id
    ).first()
    if not sale:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if not controller._extract_invoice_meta(sale.notes):
        raise HTTPException(status_code=400, detail="This sale is not an invoice")
    if not controller.is_terminal_admin(db, terminal.id, current_user.id) and sale.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Not allowed")

    items = db.query(PDVSaleItem).filter(PDVSaleItem.sale_id == sale.id).all()
    if document_type == "receipt":
        pdf_bytes = controller.generate_receipt_pdf(sale, terminal, items)
        filename = f"recibo-{sale.id}.pdf"
        whatsapp_caption = "Recibo SkyPDV"
    else:
        pdf_bytes = controller.generate_invoice_pdf(sale, terminal, items)
        filename = f"fatura-{sale.id}.pdf"
        whatsapp_caption = "Fatura SkyPDV"

    if phone:
        send_whatsapp_file(phone, filename, "application/pdf", pdf_bytes, caption=whatsapp_caption)

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'}
    )

@router.get("/sales/{sale_id}", response_model=schemas.PDVSale)
def get_sale_details(
    sale_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Detalhes de uma venda específica
    - Caixas só podem ver suas próprias vendas
    - Admins podem ver todas as vendas do terminal
    """
    terminal = controller.get_terminal_required(db, current_user.id)
    if controller.is_terminal_admin(db, terminal.id, current_user.id):
        filter_user_id = None
    else:
        filter_user_id = current_user.id
    return controller.get_sale_details(db, sale_id, terminal.id, filter_user_id)

@router.post("/sales/{sale_id}/void", response_model=schemas.PDVSale)
def void_sale(
    sale_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Anular uma venda e estornar stock/caixa"""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.void_sale(db, sale_id, terminal.id, current_user.id)

# ===================================================================
# Dashboard Endpoints
# ===================================================================

@router.get("/dashboard", response_model=schemas.DashboardStats)
def get_dashboard(
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Estatísticas do dashboard PDV (Hoje/Mês/Top Produtos)
    - Caixas veem apenas suas próprias estatísticas
    - Admins veem estatísticas de todos os caixas
    - Se user_id for fornecido e usuário for admin, filtra por esse caixa específico
    """
    terminal = controller.get_terminal_required(db, current_user.id)
    if controller.is_terminal_admin(db, terminal.id, current_user.id):
        filter_user_id = user_id
    else:
        filter_user_id = current_user.id
    return controller.get_dashboard_stats(db, terminal.id, filter_user_id)

@router.get("/reports/sales-summary", response_model=schemas.SalesSummary)
def get_sales_report(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Gerar relatório resumido de vendas para um periodo livre
    - Caixas veem apenas suas próprias vendas
    - Admins veem todas as vendas do terminal
    - Se user_id for fornecido e usuário for admin, filtra por esse caixa específico
    """
    terminal = controller.get_terminal_required(db, current_user.id)
    
    # Se não informar datas, assume mês atual
    if not start_date:
        start_date = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        # Garantir que start_date comece no início do dia se não houver tempo
        if start_date.hour == 0 and start_date.minute == 0 and start_date.second == 0:
            start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)

    if not end_date:
        end_date = datetime.utcnow()
    else:
        # Se end_date foi fornecido sem horas, assume fim do dia
        if end_date.hour == 0 and end_date.minute == 0 and end_date.second == 0:
            end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    
    # Se user_id foi fornecido e usuário é admin, usar esse user_id
    # Caso contrário, usar current_user.id (filtro automático)
    if controller.is_terminal_admin(db, terminal.id, current_user.id):
        filter_user_id = user_id
    else:
        filter_user_id = current_user.id
        
    return controller.get_sales_summary(db, terminal.id, start_date, end_date, filter_user_id)

@router.get("/reports/sales-summary.pdf")
def get_sales_report_pdf(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    user_id: Optional[int] = None,
    product_scope: str = Query("all", pattern="^(all|beverages)$"),
    phone: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Relatório geral: tabela de vendas, totais por pagamento em texto,
    tabela de serviços, totais por pagamento em texto, e tabela de saídas.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, HRFlowable
    from xml.sax.saxutils import escape as _esc
    from models import PDVServiceOrder, PDVOutflow as PDVOutflowModel, User as UserModel

    terminal = controller.get_terminal_required(db, current_user.id)

    # ── Date defaults ─────────────────────────────────────────
    if not start_date:
        start_date = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        if start_date.hour == 0 and start_date.minute == 0 and start_date.second == 0:
            start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    if not end_date:
        end_date = datetime.utcnow()
    else:
        if end_date.hour == 0 and end_date.minute == 0 and end_date.second == 0:
            end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)

    if controller.is_terminal_admin(db, terminal.id, current_user.id):
        filter_user_id = user_id
    else:
        filter_user_id = current_user.id

    currency = terminal.currency or "MT"
    issued_at = datetime.utcnow()
    period_label = f"{start_date.strftime('%d/%m/%Y')} até {end_date.strftime('%d/%m/%Y')}"

    # ── Helpers ───────────────────────────────────────────────
    def _fmt_dt(dt) -> str:
        local_dt = controller.to_mozambique_datetime(dt)
        return local_dt.strftime("%d/%m/%Y %H:%M") if local_dt else ""

    def _fmt_date(dt) -> str:
        return dt.strftime("%d/%m/%Y") if dt else ""

    def _fmt_money(v) -> str:
        try:
            return f"{float(v):,.2f}"
        except Exception:
            return "0.00"

    def _fmt_cur(v) -> str:
        return f"{_fmt_money(v)} {currency}"

    def _fmt_int(v) -> str:
        try:
            val = float(v)
            return str(int(val)) if val == int(val) else f"{val:.2f}"
        except Exception:
            return "0"

    def _has_val(v) -> bool:
        try:
            return float(v) != 0.0
        except Exception:
            return bool(v)

    # ── Beverage scope filter ─────────────────────────────────
    beverage_filter = or_(
        func.lower(func.coalesce(PDVProduct.category, "")).like("%bebida%"),
        func.lower(func.coalesce(PDVProduct.category, "")).like("%drink%"),
        func.lower(func.coalesce(PDVProduct.name, "")).like("%sumo%"),
        func.lower(func.coalesce(PDVProduct.name, "")).like("%suco%"),
        func.lower(func.coalesce(PDVProduct.name, "")).like("%agua%"),
        func.lower(func.coalesce(PDVProduct.name, "")).like("%água%"),
        func.lower(func.coalesce(PDVProduct.name, "")).like("%refrigerante%"),
        func.lower(func.coalesce(PDVProduct.name, "")).like("%cerveja%"),
        func.lower(func.coalesce(PDVProduct.name, "")).like("%vinho%"),
        func.lower(func.coalesce(PDVProduct.name, "")).like("%whisky%"),
        func.lower(func.coalesce(PDVProduct.name, "")).like("%cafe%"),
        func.lower(func.coalesce(PDVProduct.name, "")).like("%café%"),
        func.lower(func.coalesce(PDVProduct.name, "")).like("%cha%"),
        func.lower(func.coalesce(PDVProduct.name, "")).like("%chá%"),
        func.lower(func.coalesce(PDVProduct.name, "")).like("%milkshake%"),
        func.lower(func.coalesce(PDVProduct.name, "")).like("%juice%"),
        func.lower(func.coalesce(PDVProduct.name, "")).like("%soda%"),
    )
    def _apply_scope(q):
        return q.filter(beverage_filter) if product_scope == "beverages" else q

    # ── 1. Dados de Vendas de Produtos ────────────────────────
    summary = controller.get_sales_summary(db, terminal.id, start_date, end_date, filter_user_id)
    sold_products_q = (
        db.query(
            PDVProduct.id.label("product_id"),
            PDVProduct.name,
            PDVProduct.allow_decimal_quantity.label("is_weighted"),
            # The sale item is the historical record. Product.price may have
            # changed after the sale and must not be used in a sales report.
            PDVSaleItem.unit_price.label("price"),
            func.sum(PDVSaleItem.quantity).label("qty"),
            func.sum(PDVSaleItem.subtotal).label("total"),
            func.max(PDVInventory.quantity).label("stock"),
        )
        .join(PDVSale, PDVSale.id == PDVSaleItem.sale_id)
        .join(PDVProduct, PDVProduct.id == PDVSaleItem.product_id)
        .outerjoin(PDVInventory, (PDVInventory.product_id == PDVProduct.id) & (PDVInventory.terminal_id == terminal.id))
        .filter(PDVSale.terminal_id == terminal.id)
        .filter(PDVSale.created_at >= start_date)
        .filter(PDVSale.created_at <= end_date)
        .filter(PDVSale.status == "completed")
    )
    if filter_user_id:
        sold_products_q = sold_products_q.filter(PDVSale.created_by == filter_user_id)
    sold_products = (
        _apply_scope(sold_products_q)
        .group_by(PDVProduct.id, PDVProduct.name, PDVProduct.allow_decimal_quantity, PDVSaleItem.unit_price)
        .order_by(func.sum(PDVSaleItem.quantity).desc())
        .all()
    )
    product_movements = (
        _apply_scope(
            db.query(
                PDVProduct.id.label("product_id"),
                PDVStockMovement.movement_type,
                func.sum(PDVStockMovement.quantity).label("quantity"),
            )
            .join(PDVProduct, PDVProduct.id == PDVStockMovement.product_id)
            .filter(PDVStockMovement.terminal_id == terminal.id)
            .filter(PDVStockMovement.created_at >= start_date)
            .filter(PDVStockMovement.created_at <= end_date)
            .group_by(PDVProduct.id, PDVStockMovement.movement_type)
        ).all()
    )
    movement_by_product = {}
    for mv in product_movements:
        stats = movement_by_product.setdefault(mv.product_id, {"entries": 0, "exits": 0})
        amt = float(mv.quantity or 0)
        if _mt_in(mv.movement_type, MovementType.IN, MovementType.RETURN):
            stats["entries"] += amt
        elif _mt_in(mv.movement_type, MovementType.OUT, MovementType.SALE):
            stats["exits"] += abs(amt)

    total_product_revenue = sum(float(p.total or 0) for p in sold_products)
    total_product_units = sum(float(p.qty or 0) for p in sold_products if float(p.qty or 0) > 0)

    # Pagamentos de Vendas
    sales_cash      = float(summary.get("cash_sales") or 0)
    sales_card      = float(summary.get("card_sales") or 0)
    sales_skywallet = float(summary.get("skywallet_sales") or 0)
    sales_mpesa     = float(summary.get("mpesa_sales") or 0)
    sales_mixed     = float(summary.get("mixed_sales") or 0)
    sales_pay_total = sales_cash + sales_card + sales_skywallet + sales_mpesa + sales_mixed

    # The payment panel must reflect this company's configured methods, even
    # when a method has no sales in the selected period.
    company_payment_methods = (
        db.query(PDVPaymentMethod)
        .filter(PDVPaymentMethod.terminal_id == terminal.id, PDVPaymentMethod.is_active == True)
        .order_by(PDVPaymentMethod.name)
        .all()
    )
    payment_names_by_id = {method.id: method.name for method in company_payment_methods}
    company_payment_totals = {method.name: 0.0 for method in company_payment_methods}
    payment_sales_query = (
        db.query(PDVSale.payment_method_id, PDVSale.payment_method, func.sum(PDVSale.total))
        .filter(PDVSale.terminal_id == terminal.id, PDVSale.status == "completed")
        .filter(PDVSale.created_at >= start_date, PDVSale.created_at <= end_date)
    )
    if filter_user_id:
        payment_sales_query = payment_sales_query.filter(PDVSale.created_by == filter_user_id)
    for method_id, legacy_name, total_value in payment_sales_query.group_by(PDVSale.payment_method_id, PDVSale.payment_method).all():
        method_name = payment_names_by_id.get(method_id) or str(legacy_name or "Não identificado")
        company_payment_totals[method_name] = company_payment_totals.get(method_name, 0.0) + float(total_value or 0)
    sales_pay_total = sum(company_payment_totals.values())

    # ── 2. Dados de Serviços Prestados ────────────────────────
    svc_q = (
        db.query(PDVServiceOrder)
        .filter(PDVServiceOrder.terminal_id == terminal.id)
        .filter(PDVServiceOrder.created_at >= start_date)
        .filter(PDVServiceOrder.created_at <= end_date)
        .filter(PDVServiceOrder.status == "completed")
        .order_by(PDVServiceOrder.created_at.desc())
    )
    if filter_user_id:
        svc_q = svc_q.filter(PDVServiceOrder.created_by == filter_user_id)
    service_orders = svc_q.all()

    # Agrupamento de serviços por nome e por método de pagamento
    svc_by_name: dict = {}
    # Service payments follow the same company-owned method list. Never map a
    # generic legacy value (such as "card") to a bank that was not registered.
    company_service_totals = {method.name: 0.0 for method in company_payment_methods}
    company_method_names_ci = {method.name.strip().lower(): method.name for method in company_payment_methods}

    def _pm_str(pm):
        if hasattr(pm, "value"):
            return str(pm.value).lower()
        return str(pm or "").lower()

    for so in service_orders:
        s_name = so.service_name or "Serviço"
        entry = svc_by_name.setdefault(s_name, {"count": 0, "qty": 0.0, "total": 0.0})
        entry["count"] += 1
        entry["qty"] += float(so.quantity or 1)
        tot = float(so.total or 0)
        entry["total"] += tot

        raw_method_name = _pm_str(so.payment_method).strip()
        method_name = company_method_names_ci.get(raw_method_name.lower(), raw_method_name or "Não informado")
        company_service_totals[method_name] = company_service_totals.get(method_name, 0.0) + tot

    total_service_revenue = sum(v["total"] for v in svc_by_name.values())

    # ── 3. Consolidação Geral por Método de Pagamento ─────────
    # Sales may use any company-defined method, not only the historical enum names.
    grand_total_revenue = sales_pay_total + total_service_revenue

    # ── 4. Dados de Saídas ────────────────────────────────────
    REASON_LABELS = {
        "consumo_interno": "Consumo interno",
        "cafetaria": "Cafetaria",
        "cozinha": "Cozinha",
        "perda": "Perda / Avaria",
        "despesa_diaria": "Despesa diária",
        "outro": "Outro",
    }
    outflows_q = (
        db.query(PDVOutflowModel)
        .filter(PDVOutflowModel.terminal_id == terminal.id)
        .filter(PDVOutflowModel.is_active == True)
        .filter(PDVOutflowModel.created_at >= start_date)
        .filter(PDVOutflowModel.created_at <= end_date)
        .order_by(PDVOutflowModel.created_at.desc())
    )
    outflows = outflows_q.all()

    def _is_prod_outflow(o):
        v = getattr(o.outflow_type, "value", str(o.outflow_type)).lower()
        return "product" in v

    prod_outflows = [o for o in outflows if _is_prod_outflow(o)]
    cash_outflows = [o for o in outflows if not _is_prod_outflow(o)]
    total_prod_outflow_qty = sum(float(o.quantity or 0) for o in prod_outflows)
    total_cash_outflow = sum(float(o.amount or 0) for o in cash_outflows)
    total_product_outflow_sale_value = sum(float(o.quantity or 0) * float((o.product.price if o.product else 0) or 0) for o in prod_outflows)

    out_user_ids = list({o.created_by for o in outflows if o.created_by})
    out_users_map: dict = {}
    if out_user_ids:
        out_users = db.query(UserModel).filter(UserModel.id.in_(out_user_ids)).all()
        out_users_map = {u.id: (u.name or u.username or str(u.id)) for u in out_users}

    # ── 5. Fecho de Caixa e Balanço Final ─────────────────────
    net_cash_balance = comb_cash - total_cash_outflow
    net_grand_balance = grand_total_revenue - total_cash_outflow

    # ── Dados da Empresa (Terminal Settings) ──────────────────
    t_settings = terminal.settings if isinstance(terminal.settings, dict) else {}
    company_name = t_settings.get("receipt_company_name") or terminal.name or "SkyPDV"
    company_address = t_settings.get("receipt_address") or terminal.address or ""
    company_contacts = t_settings.get("receipt_contacts") or terminal.phone or ""
    company_nuit = t_settings.get("receipt_nuit") or ""

    # ── ReportLab Setup & Styles (preto / claro) ──────────────
    C_BLACK  = colors.HexColor("#111111")
    C_TEXT   = colors.HexColor("#222222")
    C_MUTED  = colors.HexColor("#555555")
    C_LINE   = colors.HexColor("#CCCCCC")

    styles = getSampleStyleSheet()
    ST_TITLE = ParagraphStyle("RTitle", parent=styles["Title"], fontSize=16, leading=20, textColor=C_BLACK, alignment=TA_LEFT, spaceAfter=2)
    ST_SUB   = ParagraphStyle("RSub",   parent=styles["Normal"], fontSize=8.5, leading=12, textColor=C_MUTED)
    ST_H2    = ParagraphStyle("RH2",    parent=styles["Heading2"], fontSize=11, leading=14, textColor=C_BLACK, spaceBefore=14, spaceAfter=6, fontName="Helvetica-Bold")
    ST_BODY  = ParagraphStyle("RBody",  parent=styles["Normal"], fontSize=9, leading=13, textColor=C_TEXT, spaceBefore=1, spaceAfter=1)
    ST_BODY_B= ParagraphStyle("RBodyB", parent=styles["Normal"], fontSize=9.5, leading=13.5, textColor=C_BLACK, spaceBefore=2, spaceAfter=2, fontName="Helvetica-Bold")
    ST_CELL  = ParagraphStyle("RCell",  parent=styles["Normal"], fontSize=8, leading=10.5, textColor=C_TEXT)
    ST_FOOT  = ParagraphStyle("RFoot",  parent=styles["Normal"], fontSize=7.5, textColor=C_MUTED, alignment=TA_CENTER)

    def _list_table_style(align_from_col=1):
        return TableStyle([
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",   (0, 0), (-1, -1), 8),
            ("TEXTCOLOR",  (0, 0), (-1, -1), C_TEXT),
            ("ALIGN",      (align_from_col, 0), (-1, -1), "RIGHT"),
            ("ALIGN",      (0, 0), (0, -1), "LEFT"),
            ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 5),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
            ("BOX",       (0, 0), (-1, -1), 0.35, C_LINE),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, C_LINE),
            ("LINEBELOW", (0, 0), (-1, 0), 0.35, C_LINE),
        ])

    def _append_payment_totals(total_label, total_val, methods):
        story.append(Paragraph(f"{total_label}: {_fmt_cur(total_val)}", ST_BODY_B))
        for name, val in methods:
            story.append(Paragraph(f"{name}: {_fmt_cur(val)}", ST_BODY))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=14*mm, rightMargin=14*mm,
        topMargin=14*mm, bottomMargin=14*mm,
    )
    story = []
    w_usable = doc.width

    # ─────────────────────────────────────────────────────────
    # CABEÇALHO
    # ─────────────────────────────────────────────────────────
    story.append(Paragraph(_esc(company_name), ST_TITLE))
    header_meta = []
    if company_address:
        header_meta.append(_esc(company_address))
    if company_contacts:
        header_meta.append(f"Tel: {_esc(company_contacts)}")
    if company_nuit:
        header_meta.append(f"NUIT: {_esc(company_nuit)}")
    if header_meta:
        story.append(Paragraph(" · ".join(header_meta), ST_SUB))
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=1, color=C_BLACK, spaceAfter=8))

    scope_label = "Apenas bebidas" if product_scope == "beverages" else "Todos os produtos"
    story.append(Paragraph("Relatório geral", ST_H2))
    story.append(Paragraph(
        f"Período: {period_label}    ·    Escopo: {scope_label}    ·    Emitido: {_fmt_dt(issued_at)}    ·    Terminal: {_esc(terminal.name or '')}",
        ST_SUB
    ))

    PAY_SALES = list(company_payment_totals.items())
    PAY_SERVICES = list(company_service_totals.items())

    # ─────────────────────────────────────────────────────────
    # 1. VENDAS
    # ─────────────────────────────────────────────────────────
    story.append(Paragraph("Vendas", ST_H2))

    prod_headers = ["Produto", "Qtd", "Preço unit.", "Total"]
    prod_col_w = [w_usable * 0.50, w_usable * 0.12, w_usable * 0.19, w_usable * 0.19]
    prod_table_data = [prod_headers]
    for product in sold_products:
        qty_sold = float(product.qty or 0)
        if qty_sold <= 0:
            continue
        price = float(getattr(product, "price", 0) or 0)
        prod_table_data.append([
            Paragraph(_esc(str(product.name or "")), ST_CELL),
            _fmt_product_quantity(product.qty, product.is_weighted),
            _fmt_money(price),
            _fmt_money(product.total or 0),
        ])
    if len(prod_table_data) == 1:
        prod_table_data.append(["Sem vendas no período", "—", "—", "—"])

    prod_tbl = Table(prod_table_data, colWidths=prod_col_w, repeatRows=1)
    prod_tbl.setStyle(_list_table_style(1))
    story.append(prod_tbl)
    story.append(Spacer(1, 8))
    _append_payment_totals("Total de vendas", sales_pay_total, PAY_SALES)

    # ─────────────────────────────────────────────────────────
    # 2. SERVIÇOS
    # ─────────────────────────────────────────────────────────
    story.append(Paragraph("Serviços", ST_H2))

    svc_headers = ["Data", "Serviço", "Cliente", "Qtd", "Método", "Total"]
    svc_cw_raw = [78, 150, 95, 32, 70, 70]
    scale_sd = w_usable / sum(svc_cw_raw)
    svc_cw = [w * scale_sd for w in svc_cw_raw]
    svc_detail_data = [svc_headers]
    for so in service_orders:
        c_name = str(so.customer_name or "Balcão")
        pm_display = _pm_str(so.payment_method).replace("_", " ").capitalize()
        svc_detail_data.append([
            _fmt_dt(so.created_at),
            Paragraph(_esc(str(so.service_name or "")), ST_CELL),
            Paragraph(_esc(c_name), ST_CELL),
            _fmt_int(so.quantity or 1),
            pm_display,
            _fmt_money(so.total or 0),
        ])
    if len(svc_detail_data) == 1:
        svc_detail_data.append(["Sem serviços no período", "—", "—", "—", "—", "—"])

    svc_tbl = Table(svc_detail_data, colWidths=svc_cw, repeatRows=1)
    svc_tbl.setStyle(_list_table_style(3))
    story.append(svc_tbl)
    story.append(Spacer(1, 8))
    _append_payment_totals("Total de serviços", total_service_revenue, PAY_SERVICES)

    # ─────────────────────────────────────────────────────────
    # 3. SAÍDAS
    # ─────────────────────────────────────────────────────────
    story.append(Paragraph("Saídas", ST_H2))

    out_headers = ["Data", "Tipo", "Descrição", "Motivo", "Destino", "Qtd", "Valor potencial", "Operador"]
    out_cw_raw = [70, 52, 120, 70, 70, 32, 62, 70]
    scale_out = w_usable / sum(out_cw_raw)
    out_cw = [w * scale_out for w in out_cw_raw]
    out_rows = [out_headers]
    for o in outflows:
        is_prod = _is_prod_outflow(o)
        tipo = "Produto" if is_prod else "Caixa"
        desc = ""
        if is_prod and o.product and o.product.name:
            desc = o.product.name
        else:
            desc = o.title or "—"
        reason = REASON_LABELS.get(o.reason or "", o.reason or "—")
        dest = o.destination or "—"
        op = out_users_map.get(o.created_by, "—") if o.created_by else "—"
        qty = _fmt_product_quantity(o.quantity or 0, bool(o.product and o.product.allow_decimal_quantity)) if is_prod else "—"
        product_sale_value = float(o.quantity or 0) * float((o.product.price if o.product else 0) or 0)
        valor = _fmt_money(o.amount or 0) if not is_prod else _fmt_money(product_sale_value)
        out_rows.append([
            _fmt_dt(o.created_at),
            tipo,
            Paragraph(_esc(str(desc)), ST_CELL),
            Paragraph(_esc(str(reason)), ST_CELL),
            Paragraph(_esc(str(dest)), ST_CELL),
            qty,
            valor,
            Paragraph(_esc(str(op)), ST_CELL),
        ])
    if len(out_rows) == 1:
        out_rows.append(["Sem saídas no período", "—", "—", "—", "—", "—", "—", "—"])

    out_tbl = Table(out_rows, colWidths=out_cw, repeatRows=1)
    out_tbl.setStyle(_list_table_style(5))
    story.append(out_tbl)
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"Total de saídas em dinheiro: {_fmt_cur(total_cash_outflow)}", ST_BODY_B))
    story.append(Paragraph(f"Quantidade total retirada: {_fmt_int(total_prod_outflow_qty)} (consulte Kg/unidade em cada produto)", ST_BODY))
    story.append(Paragraph(f"Valor potencial de venda dos produtos retirados: {_fmt_cur(total_product_outflow_sale_value)}", ST_BODY_B))

    story.append(Spacer(1, 14))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_LINE, spaceAfter=6))
    story.append(Paragraph(
        f"SkyPDV — Relatório geral gerado em {_fmt_dt(issued_at)}  |  Terminal: {_esc(terminal.name or '—')}  |  {_esc(company_name)}",
        ST_FOOT
    ))

    doc.build(story)
    pdf_bytes = buffer.getvalue()

    filename = f"Relatorio_Geral_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.pdf"
    resp_headers = {"Content-Disposition": f'inline; filename="{filename}"'}

    if phone:
        caption = f"Relatório Geral SkyPDV — {period_label}"
        send_whatsapp_file(phone, filename, "application/pdf", pdf_bytes, caption=caption)
        send_whatsapp_text(phone, caption)

    return StreamingResponse(io.BytesIO(pdf_bytes), media_type="application/pdf", headers=resp_headers)


@router.get("/reports/sales-summary.xlsx")
def get_sales_report_excel(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    user_id: Optional[int] = None,
    phone: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Exporta o relatório geral completo em Excel (XLSX):
    - Resumo Geral & Fecho Financeiro
    - Meios de Pagamento Consolidados
    - Produtos Vendidos
    - Serviços Prestados
    - Saídas Registadas
    """
    from models import PDVServiceOrder, PDVOutflow as PDVOutflowModel, User as UserModel

    terminal = controller.get_terminal_required(db, current_user.id)
    currency = terminal.currency or "MT"

    # Datas padrão
    if not start_date:
        start_date = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        if start_date.hour == 0 and start_date.minute == 0 and start_date.second == 0:
            start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)

    if not end_date:
        end_date = datetime.utcnow()
    else:
        if end_date.hour == 0 and end_date.minute == 0 and end_date.second == 0:
            end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)

    filter_user_id = user_id if controller.is_terminal_admin(db, terminal.id, current_user.id) else current_user.id

    # 1. Resumo de Vendas
    summary = controller.get_sales_summary(db, terminal.id, start_date, end_date, filter_user_id)
    sales_cash      = float(summary.get("cash_sales") or 0)
    sales_card      = float(summary.get("card_sales") or 0)
    sales_skywallet = float(summary.get("skywallet_sales") or 0)
    sales_mpesa     = float(summary.get("mpesa_sales") or 0)
    sales_mixed     = float(summary.get("mixed_sales") or 0)
    total_sales_pay = sales_cash + sales_card + sales_skywallet + sales_mpesa + sales_mixed

    # 2. Serviços Prestados
    svc_q = (
        db.query(PDVServiceOrder)
        .filter(PDVServiceOrder.terminal_id == terminal.id)
        .filter(PDVServiceOrder.created_at >= start_date)
        .filter(PDVServiceOrder.created_at <= end_date)
        .filter(PDVServiceOrder.status == "completed")
        .order_by(PDVServiceOrder.created_at.desc())
    )
    if filter_user_id:
        svc_q = svc_q.filter(PDVServiceOrder.created_by == filter_user_id)
    service_orders = svc_q.all()

    def _pm_str(pm):
        if hasattr(pm, "value"):
            return str(pm.value).lower()
        return str(pm or "").lower()

    svc_cash = 0.0
    svc_mpesa = 0.0
    svc_skywallet = 0.0
    svc_card = 0.0
    svc_other = 0.0
    for so in service_orders:
        tot = float(so.total or 0)
        pm_val = _pm_str(so.payment_method)
        if "cash" in pm_val or "dinheiro" in pm_val:
            svc_cash += tot
        elif "mpesa" in pm_val:
            svc_mpesa += tot
        elif "skywallet" in pm_val or "emola" in pm_val or "e-mola" in pm_val:
            svc_skywallet += tot
        elif "card" in pm_val or "pos" in pm_val or "bci" in pm_val or "bim" in pm_val:
            svc_card += tot
        else:
            svc_other += tot
    total_service_rev = svc_cash + svc_mpesa + svc_skywallet + svc_card + svc_other

    # 3. Saídas
    outflows_q = (
        db.query(PDVOutflowModel)
        .filter(PDVOutflowModel.terminal_id == terminal.id)
        .filter(PDVOutflowModel.is_active == True)
        .filter(PDVOutflowModel.created_at >= start_date)
        .filter(PDVOutflowModel.created_at <= end_date)
        .order_by(PDVOutflowModel.created_at.desc())
    )
    outflows = outflows_q.all()

    def _is_prod_outflow(o):
        v = getattr(o.outflow_type, "value", str(o.outflow_type)).lower()
        return "product" in v

    prod_outflows = [o for o in outflows if _is_prod_outflow(o)]
    cash_outflows = [o for o in outflows if not _is_prod_outflow(o)]
    total_prod_outflow_qty = sum(float(o.quantity or 0) for o in prod_outflows)
    total_cash_outflow = sum(float(o.amount or 0) for o in cash_outflows)

    out_user_ids = list({o.created_by for o in outflows if o.created_by})
    out_users_map = {}
    if out_user_ids:
        out_users = db.query(UserModel).filter(UserModel.id.in_(out_user_ids)).all()
        out_users_map = {u.id: (u.name or u.username or str(u.id)) for u in out_users}

    # Consolidados
    comb_cash = sales_cash + svc_cash
    comb_mpesa = sales_mpesa + svc_mpesa
    comb_skywallet = sales_skywallet + svc_skywallet
    comb_card = sales_card + svc_card
    comb_mixed = sales_mixed + svc_other
    grand_revenue = comb_cash + comb_mpesa + comb_skywallet + comb_card + comb_mixed
    net_cash_balance = comb_cash - total_cash_outflow
    net_grand_balance = grand_revenue - total_cash_outflow

    # ── Criar Workbook ────────────────────────────────────────
    wb = openpyxl.Workbook()

    # Planilha 1: Resumo Geral
    ws_summary = wb.active
    ws_summary.title = "Resumo Geral"
    summary_rows = [
        ("Terminal", terminal.name or "SkyPDV"),
        ("Período Início", start_date.strftime("%d/%m/%Y %H:%M")),
        ("Período Fim", end_date.strftime("%d/%m/%Y %H:%M")),
        ("Moeda", currency),
        ("", ""),
        ("--- INDICADORES PRINCIPAIS ---", ""),
        ("Receita Vendas de Produtos", float(summary.get("total_revenue") or 0)),
        ("Receita Serviços Prestados", total_service_rev),
        ("TOTAL ARRECADADO BRUTO", grand_revenue),
        ("Total Saídas Dinheiro (Despesas)", total_cash_outflow),
        ("SALDO FINAL EM CAIXA (Dinheiro)", net_cash_balance),
        ("RESULTADO GLOBAL LÍQUIDO", net_grand_balance),
        ("", ""),
        ("--- DETALHE DE VENDAS ---", ""),
        ("Total Transacções de Venda", summary["total_sales"]),
        ("Itens Vendidos", summary["total_items_sold"]),
        ("Ticket Médio", float(summary.get("average_sale_value") or 0)),
        ("Custo Total Estimado", float(summary.get("total_cost") or 0)),
        ("Lucro Bruto Estimado", float(summary.get("gross_profit") or 0)),
        ("Descontos Concedidos", float(summary.get("total_discounts") or 0)),
        ("Impostos", float(summary.get("total_taxes") or 0)),
        ("Vendas Anuladas", summary["voided_sales"]),
        ("Valor Anulado", float(summary.get("voided_amount") or 0)),
        ("", ""),
        ("--- DETALHE DE SERVIÇOS ---", ""),
        ("Ordens de Serviço Concluídas", len(service_orders)),
        ("Receita de Serviços", total_service_rev),
        ("", ""),
        ("--- DETALHE DE SAÍDAS ---", ""),
        ("Saídas de Produtos (Qtd)", total_prod_outflow_qty),
        ("Despesas de Caixa (Valor)", total_cash_outflow),
    ]
    ws_summary.append(["Métrica / Rubrica", "Valor"])
    for name, value in summary_rows:
        ws_summary.append([name, value])

    # Planilha 2: Meios de Pagamento Consolidados
    ws_pay = wb.create_sheet("Meios de Pagamento")
    ws_pay.append(["Método de Pagamento", "Vendas Produtos", "Serviços Prestados", "TOTAL ARRECADADO", "% Geral"])
    pay_table_data = [
        ("Dinheiro (Cash)", sales_cash, svc_cash, comb_cash),
        ("M-Pesa", sales_mpesa, svc_mpesa, comb_mpesa),
        ("E-Mola / SkyWallet", sales_skywallet, svc_skywallet, comb_skywallet),
        ("BCI POS / Cartão", sales_card, svc_card, comb_card),
        ("Misto / Outros", sales_mixed, svc_other, comb_mixed),
    ]
    for m, vp, vs, vt in pay_table_data:
        pct = (vt / grand_revenue * 100) if grand_revenue > 0 else 0.0
        ws_pay.append([m, vp, vs, vt, f"{pct:.1f}%"])
    ws_pay.append(["TOTAL GERAL", total_sales_pay, total_service_rev, grand_revenue, "100.0%"])

    # Planilha 3: Produtos Vendidos
    ws_products = wb.create_sheet("Produtos Vendidos")
    ws_products.append(["Produto", "Unidade", "Qtd Vendida", "Stock Inicial", "Entradas", "Saídas", "Preço Unit.", "Receita Total"])

    sold_products_query = (
        db.query(
            PDVProduct.id.label("product_id"),
            PDVProduct.name,
            PDVProduct.allow_decimal_quantity.label("is_weighted"),
            PDVSaleItem.unit_price.label("price"),
            func.sum(PDVSaleItem.quantity).label("qty"),
            func.sum(PDVSaleItem.subtotal).label("total"),
            func.max(PDVInventory.quantity).label("stock"),
        )
        .join(PDVSale, PDVSale.id == PDVSaleItem.sale_id)
        .join(PDVProduct, PDVProduct.id == PDVSaleItem.product_id)
        .outerjoin(PDVInventory, (PDVInventory.product_id == PDVProduct.id) & (PDVInventory.terminal_id == terminal.id))
        .filter(PDVSale.terminal_id == terminal.id)
        .filter(PDVSale.created_at >= start_date)
        .filter(PDVSale.created_at <= end_date)
        .filter(PDVSale.status == "completed")
    )
    if filter_user_id:
        sold_products_query = sold_products_query.filter(PDVSale.created_by == filter_user_id)
    sold_products = (
        sold_products_query
        .group_by(PDVProduct.id, PDVProduct.name, PDVProduct.allow_decimal_quantity, PDVSaleItem.unit_price)
        .order_by(func.sum(PDVSaleItem.quantity).desc())
        .all()
    )

    product_movements = (
        db.query(
            PDVProduct.id.label("product_id"),
            PDVStockMovement.movement_type,
            func.sum(PDVStockMovement.quantity).label("quantity"),
        )
        .join(PDVProduct, PDVProduct.id == PDVStockMovement.product_id)
        .filter(PDVStockMovement.terminal_id == terminal.id)
        .filter(PDVStockMovement.created_at >= start_date)
        .filter(PDVStockMovement.created_at <= end_date)
        .group_by(PDVProduct.id, PDVStockMovement.movement_type)
        .all()
    )
    movement_by_product = {}
    for movement in product_movements:
        stats = movement_by_product.setdefault(movement.product_id, {"entries": 0, "exits": 0})
        amount = float(movement.quantity or 0)
        if _mt_in(movement.movement_type, MovementType.IN, MovementType.RETURN):
            stats["entries"] += amount
        elif _mt_in(movement.movement_type, MovementType.OUT, MovementType.SALE):
            stats["exits"] += abs(amount)

    for product in sold_products:
        qty_sold = float(product.qty or 0)
        if qty_sold <= 0:
            continue
        price = float(getattr(product, "price", 0) or 0)
        movement_stats = movement_by_product.get(product.product_id, {"entries": 0, "exits": 0})
        entries = float(movement_stats.get("entries", 0) or 0)
        exits = float(movement_stats.get("exits", 0) or 0)
        current_stock = float(product.stock or 0)
        initial_stock = current_stock - (entries - exits)
        rev = float(product.total or 0)
        ws_products.append([
            str(product.name or ""),
            "Kg" if product.is_weighted else "Un.",
            _fmt_product_quantity(product.qty, product.is_weighted),
            float(initial_stock),
            float(entries),
            float(exits),
            float(price),
            float(rev),
        ])

    # Planilha 4: Serviços Prestados
    ws_services = wb.create_sheet("Serviços Prestados")
    ws_services.append(["Recibo / ID", "Data / Hora", "Serviço", "Cliente", "Qtd", "Método Pagamento", "Total"])
    for so in service_orders:
        rec = str(so.receipt_number or f"#{so.id}")
        c_name = str(so.customer_name or "Balcão")
        ws_services.append([
            rec,
            so.created_at.strftime("%d/%m/%Y %H:%M") if so.created_at else "",
            str(so.service_name or ""),
            c_name,
            float(so.quantity or 1),
            _pm_str(so.payment_method).capitalize(),
            float(so.total or 0),
        ])

    # Planilha 5: Saídas Registadas (Produtos Agrupados + Despesas de Caixa)
    ws_outflows = wb.create_sheet("Saídas Registadas")
    REASON_LABELS = {
        "consumo_interno": "Consumo interno",
        "cafetaria": "Cafetaria",
        "cozinha": "Cozinha",
        "perda": "Perda / Avaria",
        "despesa_diaria": "Despesa diária",
        "outro": "Outro",
    }
    ws_outflows.append(["--- PRODUTOS RETIRADOS (AGRUPADOS POR PRODUTO) ---"])
    ws_outflows.append(["Produto", "Qtd Total Levada", "N.º Saídas", "Motivo(s)", "Destino(s)", "Operador(es)"])
    excel_prod_grouped = {}
    for o in prod_outflows:
        key = o.product_id or (o.product.name if o.product else o.title) or "Outro"
        p_name = o.product.name if (o.product and o.product.name) else (o.title or "Produto")
        qty = float(o.quantity or 0)
        reason = REASON_LABELS.get(o.reason or "", o.reason or "")
        dest = o.destination or ""
        op = out_users_map.get(o.created_by, "") if o.created_by else ""
        if key not in excel_prod_grouped:
            excel_prod_grouped[key] = {
                "name": p_name, "qty": 0.0, "count": 0, "reasons": set(), "dests": set(), "ops": set()
            }
        e = excel_prod_grouped[key]
        e["qty"] += qty
        e["count"] += 1
        if reason: e["reasons"].add(reason)
        if dest: e["dests"].add(dest)
        if op: e["ops"].add(op)

    for _, p_data in sorted(excel_prod_grouped.items(), key=lambda x: -x[1]["qty"]):
        ws_outflows.append([
            p_data["name"],
            float(p_data["qty"]),
            int(p_data["count"]),
            ", ".join(sorted(p_data["reasons"])) or "—",
            ", ".join(sorted(p_data["dests"])) or "—",
            ", ".join(sorted(p_data["ops"])) or "—",
        ])
    ws_outflows.append(["TOTAL PRODUTOS RETIRADOS", float(total_prod_outflow_qty), len(prod_outflows), "", "", ""])
    ws_outflows.append([])
    ws_outflows.append(["--- DESPESAS DE CAIXA (SAÍDAS EM DINHEIRO) ---"])
    ws_outflows.append(["ID", "Data / Hora", "Motivo", "Descrição / Item", "Destino", "Operador", "Valor"])
    for o in cash_outflows:
        motivo = REASON_LABELS.get(o.reason or "", o.reason or "—")
        operador = out_users_map.get(o.created_by, "—") if o.created_by else "—"
        ws_outflows.append([
            f"#{o.id}",
            o.created_at.strftime("%d/%m/%Y %H:%M") if o.created_at else "",
            motivo,
            o.title or "—",
            o.destination or "—",
            operador,
            float(o.amount or 0),
        ])
    ws_outflows.append(["TOTAL DESPESAS DE CAIXA", "", "", "", "", len(cash_outflows), float(total_cash_outflow)])

    # Auto ajuste de colunas em todas as planilhas
    for sheet in wb.worksheets:
        for col in sheet.columns:
            col_letter = get_column_letter(col[0].column)
            max_len = max(len(str(c.value or "")) for c in col)
            sheet.column_dimensions[col_letter].width = max(max_len + 3, 10)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    filename = f"Relatorio_Geral_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.xlsx"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Access-Control-Expose-Headers": "Content-Disposition",
    }

    if phone:
        caption = f"Relatório Geral SkyPDV (Excel) {start_date.strftime('%d/%m/%Y')} a {end_date.strftime('%d/%m/%Y')}."
        send_whatsapp_file(phone, filename, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer.getvalue(), caption=caption)
        send_whatsapp_text(phone, caption)

    return StreamingResponse(buffer, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)


@router.get("/reports/products.pdf")
def get_products_report_pdf(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from decimal import Decimal
    from models import PDVProduct

    def _fmt_dt(dt: Optional[datetime]) -> str:
        local_dt = controller.to_mozambique_datetime(dt)
        if not local_dt:
            return ""
        return local_dt.strftime("%d/%m/%Y %H:%M")

    def _fmt_date(dt: Optional[datetime]) -> str:
        if not dt:
            return ""
        return dt.strftime("%d/%m/%Y")

    def _fmt_money(v) -> str:
        if v is None:
            return "0.00 MT"
        try:
            val = float(v)
            return f"{val:,.2f} MT"
        except Exception:
            return f"{str(v)} MT"

    def _fmt_qty(v) -> str:
        if v is None:
            return "0"
        try:
            val = float(v)
            if val == int(val):
                return f"{int(val):,}"
            return f"{val:,.2f}"
        except Exception:
            return str(v)

    issued_at = datetime.utcnow()

    products = (
        db.query(PDVProduct)
        .filter(PDVProduct.terminal_id == terminal.id)
        .filter(PDVProduct.is_active == True)
        .order_by(PDVProduct.name.asc())
        .all()
    )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("Relatório: Produtos e Estoque", styles["Title"]))
    story.append(Paragraph(f"Emitido em: {_fmt_dt(issued_at)} (UTC)", styles["Normal"]))
    story.append(Spacer(1, 12))

    table_data = [["Produto", "Estoque", "Preço Unitário", "Total"]]
    total_items_count = 0
    total_stock_qty = Decimal("0.00")
    total_stock_value = Decimal("0.00")

    for p in products:
        inv_qty = None
        qty_dec = Decimal("0.00")
        if getattr(p, "track_stock", False):
            inv = getattr(p, "inventory", None)
            inv_qty = getattr(inv, "quantity", None) if inv else Decimal("0.00")
            try:
                qty_dec = Decimal(str(inv_qty)) if inv_qty is not None else Decimal("0.00")
            except Exception:
                qty_dec = Decimal("0.00")

        try:
            price_dec = Decimal(str(getattr(p, "price", 0) or 0))
        except Exception:
            price_dec = Decimal("0.00")

        row_total = qty_dec * price_dec if getattr(p, "track_stock", False) else Decimal("0.00")

        total_items_count += 1
        total_stock_qty += qty_dec
        total_stock_value += row_total

        table_data.append(
            [
                str(getattr(p, "name", "") or ""),
                _fmt_product_quantity(qty_dec, getattr(p, "allow_decimal_quantity", False)) if getattr(p, "track_stock", False) else "-",
                _fmt_money(price_dec),
                _fmt_money(row_total) if getattr(p, "track_stock", False) else "-",
            ]
        )

    # Linha de totalização na tabela
    table_data.append(
        [
            "TOTAL GERAL",
            _fmt_qty(total_stock_qty),
            "",
            _fmt_money(total_stock_value)
        ]
    )

    t_styles = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9.5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#E2E8F0")),
    ]

    # Zebra striping para facilitar a leitura linha a linha
    for i in range(1, len(table_data) - 1):
        if i % 2 == 0:
            t_styles.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F8FAFC")))

    products_table = Table(table_data, colWidths=[220, 80, 110, 110], repeatRows=1)
    products_table.setStyle(TableStyle(t_styles))

    story.append(products_table)
    story.append(Spacer(1, 14))

    # Métricas de resumo abaixo da tabela
    summary_data = [
        ["Total de Produtos Cadastrados:", f"{total_items_count:,} produtos"],
        ["Total de Itens em Estoque:", f"{_fmt_qty(total_stock_qty)} unidades"],
        ["Valor Total do Estoque:", f"{_fmt_money(total_stock_value)}"],
    ]
    summary_table = Table(summary_data, colWidths=[240, 280])
    summary_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, 2), (1, 2), colors.HexColor("#166534")),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(summary_table)

    doc.build(story)
    pdf_bytes = buffer.getvalue()

    filename = f"products_{_fmt_date(issued_at)}.pdf"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Access-Control-Expose-Headers": "Content-Disposition",
    }
    return StreamingResponse(io.BytesIO(pdf_bytes), media_type="application/pdf", headers=headers)

@router.get("/reports/periodic", response_model=schemas.SalesSummary)
def get_periodic_sales_report(
    period: str = Query(..., description="Tipo de periodo: day, month, year"),
    date: str = Query(..., description="Data no formato AAAA-MM-DD, AAAA-MM ou AAAA"),
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Relatório simplificado por Dia, Mês ou Ano.
    - Ex: period='day', date='2024-01-21'
    - Ex: period='month', date='2024-01'
    - Ex: period='year', date='2024'
    - Caixas veem apenas suas próprias vendas
    - Admins veem todas as vendas do terminal
    - Se user_id for fornecido e usuário for admin, filtra por esse caixa específico
    """
    terminal = controller.get_terminal_required(db, current_user.id)
    # Se user_id foi fornecido e usuário é admin, usar esse user_id
    filter_user_id = user_id if controller.is_terminal_admin(db, terminal.id, current_user.id) else current_user.id
    return controller.get_periodic_report(db, terminal.id, period, date, filter_user_id)

@router.get("/reports/detailed-monthly", response_model=schemas.DetailedMonthlyReport)
def get_detailed_monthly_report(
    year: int = Query(..., description="Ano (ex: 2024)"),
    month: int = Query(..., description="Mês (1-12)"),
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Relatório mensal detalhado com breakdown diário, top produtos, categorias, etc.
    - Caixas veem apenas suas próprias vendas
    - Admins veem todas as vendas do terminal
    - Se user_id for fornecido e usuário for admin, filtra por esse caixa específico
    """
    terminal = controller.get_terminal_required(db, current_user.id)
    filter_user_id = user_id if controller.is_terminal_admin(db, terminal.id, current_user.id) else current_user.id
    return controller.get_detailed_monthly_report(db, terminal.id, year, month, filter_user_id)

@router.get("/reports/detailed-yearly", response_model=schemas.DetailedYearlyReport)
def get_detailed_yearly_report(
    year: int = Query(..., description="Ano (ex: 2024)"),
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Relatório anual detalhado com breakdown mensal, comparação, tendências, etc.
    - Caixas veem apenas suas próprias vendas
    - Admins veem todas as vendas do terminal
    - Se user_id for fornecido e usuário for admin, filtra por esse caixa específico
    """
    terminal = controller.get_terminal_required(db, current_user.id)
    filter_user_id = user_id if controller.is_terminal_admin(db, terminal.id, current_user.id) else current_user.id
    return controller.get_detailed_yearly_report(db, terminal.id, year, filter_user_id)

@router.get("/reports/top-products", response_model=List[schemas.TopProduct])
def get_top_products_report(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = Query(20, description="Número de produtos a retornar"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Relatorio de produtos mais vendidos em um periodo.
    Se não informar datas, assume mês atual.
    """
    terminal = controller.get_terminal_required(db, current_user.id)
    if not start_date:
        start_date = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0)
    if not end_date:
        end_date = datetime.utcnow()
    return controller.get_top_products_report(db, terminal.id, start_date, end_date, limit)

@router.get("/reports/sales-by-day", response_model=List[schemas.SalesByPeriod])
def get_sales_by_day(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Breakdown de vendas por dia em um periodo.
    Útil para gráficos de tendência diária.
    Se não informar datas, assume mês atual.
    """
    terminal = controller.get_terminal_required(db, current_user.id)
    if not start_date:
        start_date = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0)
    if not end_date:
        end_date = datetime.utcnow()
    if controller.is_terminal_admin(db, terminal.id, current_user.id):
        filter_user_id = user_id
    else:
        filter_user_id = current_user.id
    return controller.get_sales_by_day(db, terminal.id, start_date, end_date, filter_user_id)

# ===================================================================
# Payment Methods Endpoints
# ===================================================================

@router.get("/payment-methods", response_model=List[schemas.PDVPaymentMethod])
def list_payment_methods(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Listar todos os métodos de pagamento cadastrados"""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.get_payment_methods_list(db, terminal.id)

@router.post("/payment-methods", response_model=schemas.PDVPaymentMethod)
def create_payment_method(
    method: schemas.PDVPaymentMethodCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Criar novo método de pagamento (pessoal ou global)"""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.create_payment_method(db, method, terminal.id, current_user.id)

@router.post("/payment-methods/{method_id}/adopt", response_model=schemas.PDVPaymentMethod)
def adopt_payment_method(
    method_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Adotar um método de pagamento global para o seu terminal"""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.adopt_payment_method(db, method_id, terminal.id, current_user.id)

@router.put("/payment-methods/{method_id}", response_model=schemas.PDVPaymentMethod)
def update_payment_method(
    method_id: int,
    updates: schemas.PDVPaymentMethodUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Atualizar método de pagamento"""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.update_payment_method(db, method_id, updates, terminal.id)

@router.delete("/payment-methods/{method_id}")
def delete_payment_method(
    method_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Desativar método de pagamento"""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.delete_payment_method(db, method_id, terminal.id)

# ===================================================================
# Finance Endpoints (apenas admin do terminal: dono ou role ADMIN)
# ===================================================================

def _require_terminal_finance_admin(db: Session, terminal, current_user_id: int) -> None:
    if not controller.is_terminal_admin(db, terminal.id, current_user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas administradores do terminal podem aceder às finanças.",
        )


@router.get("/finance/expense-categories", response_model=List[schemas.PDVExpenseCategory])
def list_expense_categories(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    _require_terminal_finance_admin(db, terminal, current_user.id)
    return controller.get_expense_categories_list(db, terminal.id)


@router.post("/finance/expense-categories", response_model=schemas.PDVExpenseCategory)
def create_expense_category(
    category: schemas.PDVExpenseCategoryCreate,
    is_global: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    _require_terminal_finance_admin(db, terminal, current_user.id)
    return controller.create_expense_category(db, category, terminal.id, current_user.id, is_global)


@router.put("/finance/expense-categories/{category_id}", response_model=schemas.PDVExpenseCategory)
def update_expense_category(
    category_id: int,
    updates: schemas.PDVExpenseCategoryUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    _require_terminal_finance_admin(db, terminal, current_user.id)
    return controller.update_expense_category(db, category_id, updates, terminal.id)


@router.delete("/finance/expense-categories/{category_id}")
def delete_expense_category(
    category_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    _require_terminal_finance_admin(db, terminal, current_user.id)
    return controller.delete_expense_category(db, category_id, terminal.id)


@router.get("/finance/expenses", response_model=List[schemas.PDVExpense])
def list_expenses(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    category_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    _require_terminal_finance_admin(db, terminal, current_user.id)
    return controller.get_expenses(db, terminal.id, start_date, end_date, category_id, skip, limit)


@router.post("/finance/expenses", response_model=schemas.PDVExpense)
def create_expense(
    expense: schemas.PDVExpenseCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    _require_terminal_finance_admin(db, terminal, current_user.id)
    return controller.create_expense(db, expense, terminal.id, current_user.id)


@router.put("/finance/expenses/{expense_id}", response_model=schemas.PDVExpense)
def update_expense(
    expense_id: int,
    updates: schemas.PDVExpenseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    _require_terminal_finance_admin(db, terminal, current_user.id)
    return controller.update_expense(db, expense_id, updates, terminal.id)


@router.delete("/finance/expenses/{expense_id}")
def delete_expense(
    expense_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    _require_terminal_finance_admin(db, terminal, current_user.id)
    return controller.delete_expense(db, expense_id, terminal.id)


@router.get("/finance/summary", response_model=schemas.FinancialSummary)
def get_finance_summary(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    _require_terminal_finance_admin(db, terminal, current_user.id)
    if not start_date:
        start_date = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if not end_date:
        end_date = datetime.utcnow()
    filter_user_id = user_id
    return controller.get_financial_summary(db, terminal.id, start_date, end_date, filter_user_id)


@router.get("/finance/tax-summary", response_model=schemas.PDVTaxSummary)
def get_finance_tax_summary(
    year: int,
    month: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    _require_terminal_finance_admin(db, terminal, current_user.id)
    return controller.get_tax_summary(db, terminal.id, year, month)


@router.put("/finance/tax-summary", response_model=schemas.PDVTaxSummary)
def update_finance_tax_summary(
    year: int,
    month: int,
    payload: schemas.PDVTaxSummaryUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)
    _require_terminal_finance_admin(db, terminal, current_user.id)
    return controller.update_tax_summary(db, terminal.id, year, month, current_user.id, payload)

@router.get("/finance/summary.pdf")
def get_finance_summary_pdf(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    user_id: Optional[int] = None,
    phone: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    terminal = controller.get_terminal_required(db, current_user.id)
    _require_terminal_finance_admin(db, terminal, current_user.id)
    if not start_date:
        start_date = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if not end_date:
        end_date = datetime.utcnow()
    filter_user_id = user_id

    summary = controller.get_financial_summary(db, terminal.id, start_date, end_date, filter_user_id)

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    currency = terminal.currency or "MT"

    def fmt_amt(v):
        try:
            return f"{float(v):,.2f} {currency}"
        except Exception:
            return f"{v} {currency}"

    story = []
    title = f"Resumo Financeiro - {terminal.name or 'SkyPDV'}"
    story.append(Paragraph(title, styles["Title"]))
    story.append(Spacer(1, 8))
    story.append(
        Paragraph(
            f"Periodo: {start_date.strftime('%d/%m/%Y')} a {end_date.strftime('%d/%m/%Y')}",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 12))

    # Totais
    totals_data = [
        ["Entradas (vendas)", fmt_amt(summary["gross_revenue"])],
        ["Lucro bruto", fmt_amt(summary["gross_profit"])],
        ["Saídas (despesas)", fmt_amt(summary["total_expenses"])],
        ["Lucro líquido", fmt_amt(summary["net_profit"])],
        ["Nº vendas", str(summary["sales_count"])],
        ["Nº despesas", str(summary["expenses_count"])],
    ]
    totals_table = Table([["Métrica", "Valor"]] + totals_data, colWidths=[220, 200])
    totals_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F2F2")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("ALIGN", (1, 1), (1, -1), "RIGHT"),
            ]
        )
    )
    story.append(totals_table)
    story.append(Spacer(1, 12))

    # Breakdown
    breakdown = summary.get("expense_breakdown") or []
    story.append(Paragraph("Despesas por categoria", styles["Heading3"]))
    if breakdown:
        rows = [["Categoria", "Valor"]]
        for item in breakdown:
            rows.append(
                [
                    item.get("category_name") or "Sem categoria",
                    fmt_amt(item.get("total_amount") or 0),
                ]
            )
        table = Table(rows, colWidths=[260, 160])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F2F2")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                ]
            )
        )
        story.append(table)
    else:
        story.append(Paragraph("Sem despesas no periodo.", styles["Normal"]))

    doc.build(story)
    buffer.seek(0)
    filename = f"financeiro_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.pdf"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}

    if phone:
        caption = f"Resumo financeiro {start_date.strftime('%d/%m/%Y')} a {end_date.strftime('%d/%m/%Y')} (SkyPDV)."
        send_whatsapp_file(phone, filename, "application/pdf", pdf_bytes, caption=caption)
        send_whatsapp_text(phone, caption)

    return StreamingResponse(io.BytesIO(pdf_bytes), media_type="application/pdf", headers=headers)


@router.get("/reports/stock-day.pdf")
def get_stock_day_report_pdf(
    date: Optional[datetime] = None,
    product_scope: str = Query("all", pattern="^(all|beverages|important)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    terminal = controller.get_terminal_required(db, current_user.id)

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from decimal import Decimal
    from models import PDVProduct

    def _fmt_dt(dt: Optional[datetime]) -> str:
        local_dt = controller.to_mozambique_datetime(dt)
        if not local_dt:
            return ""
        return local_dt.strftime("%d/%m/%Y %H:%M")

    def _fmt_num(v, digits: int = 3) -> str:
        if v is None:
            return f"{0:.{digits}f}"
        if isinstance(v, bool):
            return f"{1 if v else 0:.{digits}f}"
        if isinstance(v, int):
            return f"{v:.{digits}f}"
        if isinstance(v, Decimal):
            return f"{v:.{digits}f}"
        try:
            return f"{float(v):.{digits}f}"
        except Exception:
            return str(v)

    def _fmt_qty(v) -> str:
        """Format quantity without forcing trailing .000 for integers."""
        try:
            value = float(v or 0)
        except Exception:
            return str(v)
        if value.is_integer():
            return str(int(value))
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        return text or "0"

    report_day = date or datetime.utcnow()
    start_date = report_day.replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = report_day.replace(hour=23, minute=59, second=59, microsecond=999999)
    issued_at = datetime.utcnow()
    if product_scope == "beverages":
        scope_label = "Apenas bebidas"
    elif product_scope == "important":
        scope_label = "Apenas produtos com estoque controlado"
    else:
        scope_label = "Todos os produtos"

    def _is_beverage_product(product: PDVProduct) -> bool:
        category = str(getattr(product, "category", "") or "").lower()
        name = str(getattr(product, "name", "") or "").lower()
        keywords = [
            "bebida", "drink", "sumo", "suco", "agua", "água", "refrigerante",
            "cerveja", "vinho", "whisky", "cafe", "café", "cha", "chá",
            "milkshake", "juice", "soda",
        ]
        return any(k in category for k in keywords) or any(k in name for k in keywords)

    inventory_rows = (
        db.query(PDVInventory, PDVProduct)
        .join(PDVProduct, PDVProduct.id == PDVInventory.product_id)
        .filter(PDVInventory.terminal_id == terminal.id)
        .filter(PDVProduct.is_active == True)
        .filter(PDVProduct.track_stock == True)
        .order_by(PDVProduct.name.asc(), PDVInventory.storage_location.asc())
        .all()
    )

    movement_rows = (
        db.query(PDVStockMovement, PDVProduct)
        .join(PDVProduct, PDVProduct.id == PDVStockMovement.product_id)
        .filter(PDVStockMovement.terminal_id == terminal.id)
        .filter(PDVStockMovement.created_at >= start_date)
        .filter(PDVStockMovement.created_at <= end_date)
        .order_by(PDVStockMovement.created_at.desc())
        .all()
    )

    if product_scope == "beverages":
        inventory_rows = [(inv, prod) for inv, prod in inventory_rows if _is_beverage_product(prod)]
        movement_rows = [(mov, prod) for mov, prod in movement_rows if _is_beverage_product(prod)]
    elif product_scope == "important":
        movement_rows = [(mov, prod) for mov, prod in movement_rows if bool(getattr(prod, "track_stock", False))]

    totals = {"entries": 0.0, "exits": 0.0, "adjustments": 0.0, "transfers": 0.0, "sales": 0.0}
    for movement, _product in movement_rows:
        qty = abs(float(movement.quantity or 0))
        if _mt_in(movement.movement_type, MovementType.IN, MovementType.RETURN):
            totals["entries"] += qty
        elif _mt_in(movement.movement_type, MovementType.SALE):
            totals["sales"] += qty
            totals["exits"] += qty
        elif _mt_in(movement.movement_type, MovementType.OUT):
            totals["exits"] += qty
        elif _mt_in(movement.movement_type, MovementType.ADJUSTMENT):
            totals["adjustments"] += qty
        elif _mt_in(movement.movement_type, MovementType.TRANSFER):
            totals["transfers"] += qty

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=28, rightMargin=28, topMargin=28, bottomMargin=28)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("Relatorio: Stock do Dia", styles["Title"]))
    story.append(Paragraph(terminal.name or "SkyPDV", styles["Heading2"]))
    story.append(Paragraph(f"Data do stock: {start_date.strftime('%d/%m/%Y')}", styles["Normal"]))
    story.append(Paragraph(f"Escopo dos produtos: {scope_label}", styles["Normal"]))
    story.append(Paragraph(f"Emitido em: {_fmt_dt(issued_at)}", styles["Normal"]))
    story.append(Spacer(1, 12))

    summary_rows = [
        ["Resumo", "Quantidade"],
        ["Entradas", _fmt_num(totals["entries"], 0)],
        ["Saidas", _fmt_num(totals["exits"], 0)],
        ["Vendido", _fmt_num(totals["sales"], 0)],
        ["Ajustes", _fmt_num(totals["adjustments"], 0)],
        ["Transferencias", _fmt_num(totals["transfers"], 0)],
        ["Movimentos do dia", str(len(movement_rows))],
        ["Itens em estoque", str(len(inventory_rows))],
    ]
    summary_table = Table(summary_rows, colWidths=[260, 220])
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F2F2")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("ALIGN", (1, 1), (1, -1), "RIGHT"),
            ]
        )
    )
    story.append(summary_table)
    story.append(Spacer(1, 12))

    product_summary = {}
    for inventory, product in inventory_rows:
        product_name = str(product.name or "")
        summary = product_summary.setdefault(
            product_name,
            {"entries": 0.0, "exits": 0.0, "current_stock": 0.0},
        )
        summary["current_stock"] += float(inventory.quantity or 0)

    for movement, product in movement_rows:
        product_name = str(product.name or "")
        summary = product_summary.setdefault(
            product_name,
            {"entries": 0.0, "exits": 0.0, "current_stock": 0.0},
        )
        qty = abs(float(movement.quantity or 0))
        if _mt_in(movement.movement_type, MovementType.IN, MovementType.RETURN):
            summary["entries"] += qty
        elif _mt_in(movement.movement_type, MovementType.OUT, MovementType.SALE):
            summary["exits"] += qty

    stock_rows = [["Produto", "Entradas", "Saidas", "Estoque atual"]]
    sorted_products = sorted(
        product_summary.items(),
        key=lambda item: (
            0 if item[1]["exits"] > 0 else 1,  # Produtos vendidos primeiro
            -item[1]["exits"],                 # Maior saída no topo
            item[0].lower(),
        ),
    )
    sold_row_indexes = []
    for product_name, summary in sorted_products:
        row_index = len(stock_rows)
        stock_rows.append(
            [
                product_name,
                _fmt_num(summary["entries"], 0),
                _fmt_num(summary["exits"], 0),
                _fmt_qty(summary["current_stock"]),
            ]
        )
        if summary["exits"] > 0:
            sold_row_indexes.append(row_index)
    if len(stock_rows) == 1:
        stock_rows.append(["Sem estoque controlado", "0", "0", "0"])

    story.append(Paragraph("Estoque por produto (Entradas/Saidas/Atual)", styles["Heading3"]))
    stock_table = Table(stock_rows, colWidths=[240, 90, 90, 90])
    stock_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F2F2")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
            ]
        )
    )
    for row_index in sold_row_indexes:
        stock_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, row_index), (-1, row_index), colors.HexColor("#E8F5E9")),
                    ("TEXTCOLOR", (0, row_index), (-1, row_index), colors.HexColor("#1B5E20")),
                ]
            )
        )
    story.append(stock_table)
    story.append(Spacer(1, 12))

    movement_table_rows = [["Hora", "Produto", "Tipo", "Qtd", "Obs"]]
    for movement, product in movement_rows[:80]:
        movement_table_rows.append(
            [
                controller.to_mozambique_datetime(movement.created_at).strftime("%H:%M"),
                str(product.name or ""),
                str(movement.movement_type or ""),
                _fmt_num(abs(float(movement.quantity or 0))),
                str(movement.notes or movement.reference or ""),
            ]
        )
    if len(movement_table_rows) == 1:
        movement_table_rows.append(["-", "Sem movimentos hoje", "-", "0", "-"])

    story.append(Paragraph("Movimentos do dia", styles["Heading3"]))
    movement_table = Table(movement_table_rows, colWidths=[55, 170, 75, 55, 175])
    movement_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F2F2")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (3, 1), (3, -1), "RIGHT"),
            ]
        )
    )
    story.append(movement_table)

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    filename = f"Stock_Dia_{start_date.strftime('%Y%m%d')}.pdf"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(io.BytesIO(pdf_bytes), media_type="application/pdf", headers=headers)


@router.get("/finance/summary.xlsx")
def get_finance_summary_excel(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    user_id: Optional[int] = None,
    phone: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    terminal = controller.get_terminal_required(db, current_user.id)
    _require_terminal_finance_admin(db, terminal, current_user.id)
    if not start_date:
        start_date = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if not end_date:
        end_date = datetime.utcnow()
    filter_user_id = user_id

    summary = controller.get_financial_summary(db, terminal.id, start_date, end_date, filter_user_id)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Resumo Financeiro"

    ws.append(["Periodo", f"{start_date.strftime('%d/%m/%Y')} a {end_date.strftime('%d/%m/%Y')}"])
    ws.append([])
    ws.append(["Métrica", "Valor"])
    ws.append(["Entradas (vendas)", summary["gross_revenue"]])
    ws.append(["Lucro bruto", summary["gross_profit"]])
    ws.append(["Saídas (despesas)", summary["total_expenses"]])
    ws.append(["Lucro líquido", summary["net_profit"]])
    ws.append(["Nº vendas", summary["sales_count"]])
    ws.append(["Nº despesas", summary["expenses_count"]])

    ws.append([])
    ws.append(["Despesas por categoria"])
    ws.append(["Categoria", "Valor"])
    for item in summary.get("expense_breakdown") or []:
        ws.append([item.get("category_name") or "Sem categoria", item.get("total_amount") or 0])

    # Ajustar larguras
    for col in range(1, 3):
        ws.column_dimensions[get_column_letter(col)].width = 28

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    filename = f"financeiro_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.xlsx"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}

    if phone:
        caption = f"Resumo financeiro (Excel) {start_date.strftime('%d/%m/%Y')} a {end_date.strftime('%d/%m/%Y')} (SkyPDV)."
        send_whatsapp_file(phone, filename, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", bio.getvalue(), caption=caption)
        send_whatsapp_text(phone, caption)

    return StreamingResponse(bio, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)


# ===================================================================
# FastFood stubs (compat for SkyPDV frontend)
# ===================================================================


@router.post("/fastfood/restaurants")
async def create_fastfood_restaurant(
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


@router.get("/fastfood/restaurants/mine")
def list_my_restaurants(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return []


# ===================================================================
# Accounts (Contas)
# ===================================================================

@router.get("/accounts", response_model=List[schemas.PDVAccount])
def list_accounts(
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return controller.get_accounts(db, current_user.id, status)


@router.post("/accounts", response_model=schemas.PDVAccount)
def create_account(
    data: schemas.PDVAccountCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return controller.create_account(db, data, current_user.id)


@router.get("/accounts/{account_id}", response_model=schemas.PDVAccount)
def get_account(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return controller.get_account(db, account_id, current_user.id)


@router.put("/accounts/{account_id}", response_model=schemas.PDVAccount)
def update_account(
    account_id: int,
    data: schemas.PDVAccountUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return controller.update_account(db, account_id, data, current_user.id)


@router.post("/accounts/{account_id}/items", response_model=schemas.PDVAccount)
def add_account_items(
    account_id: int,
    items: List[schemas.PDVAccountItemCreate],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return controller.add_items_to_account(db, account_id, items, current_user.id)


@router.patch("/accounts/{account_id}/items/{item_id}", response_model=schemas.PDVAccount)
def update_account_item(
    account_id: int,
    item_id: int,
    data: schemas.PDVAccountItemUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return controller.update_account_item(db, account_id, item_id, data, current_user.id)


@router.delete("/accounts/{account_id}/items/{item_id}", response_model=schemas.PDVAccount)
def remove_account_item(
    account_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return controller.remove_account_item(db, account_id, item_id, current_user.id)


@router.post("/accounts/{account_id}/close", response_model=schemas.PDVAccount)
def close_account(
    account_id: int,
    data: schemas.PDVAccountClose,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return controller.close_account(db, account_id, data, current_user.id)


@router.delete("/accounts/{account_id}")
def delete_account(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return controller.delete_account(db, account_id, current_user.id)


# ===================================================================
# Services (Serviços) Endpoints
# ===================================================================

@router.get("/services", response_model=List[schemas.PDVServiceResponse])
def get_services(
    search: Optional[str] = None,
    is_active: Optional[bool] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Listar catálogo de serviços do terminal."""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.get_services(
        db, terminal.id, search=search, is_active=is_active, skip=skip, limit=limit
    )


@router.post("/services", response_model=schemas.PDVServiceResponse)
def create_service(
    data: schemas.PDVServiceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Criar novo serviço no catálogo."""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.create_service(db, terminal.id, data, user_id=current_user.id)


@router.put("/services/{service_id}", response_model=schemas.PDVServiceResponse)
def update_service(
    service_id: int,
    data: schemas.PDVServiceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Actualizar um serviço existente."""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.update_service(db, terminal.id, service_id, data)


@router.delete("/services/{service_id}")
def delete_service(
    service_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Desactivar um serviço."""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.delete_service(db, terminal.id, service_id)


# ===================================================================
# Service Orders (Serviços Prestados) Endpoints
# ===================================================================

@router.post("/service-orders", response_model=schemas.PDVServiceOrderResponse)
def create_service_order(
    data: schemas.PDVServiceOrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Registar um serviço prestado e debitar no caixa actual."""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.create_service_order(db, terminal.id, current_user.id, data)


@router.get("/service-orders", response_model=List[schemas.PDVServiceOrderResponse])
def get_service_orders(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=2000),
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    service_id: Optional[int] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Listar histórico de serviços prestados."""
    terminal = controller.get_terminal_required(db, current_user.id)
    filter_user_id = None
    if not controller.is_terminal_admin(db, terminal.id, current_user.id):
        filter_user_id = current_user.id
    return controller.get_service_orders(
        db, terminal.id, skip=skip, limit=limit,
        start_date=start_date, end_date=end_date,
        service_id=service_id, user_id=filter_user_id, status=status
    )


@router.get("/service-orders/summary", response_model=schemas.PDVServiceSummary)
def get_service_summary(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Resumo estatístico e receita de serviços prestados."""
    terminal = controller.get_terminal_required(db, current_user.id)
    filter_user_id = None
    if not controller.is_terminal_admin(db, terminal.id, current_user.id):
        filter_user_id = current_user.id
    return controller.get_service_summary(
        db, terminal.id, start_date=start_date, end_date=end_date, user_id=filter_user_id
    )


@router.get("/service-orders/{order_id}", response_model=schemas.PDVServiceOrderResponse)
def get_service_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Obter detalhes de um serviço prestado específico."""
    terminal = controller.get_terminal_required(db, current_user.id)
    return controller.get_service_order(db, terminal.id, order_id)


# ---------------------------------------------------------------------------
# PDF export — Service Orders
# ---------------------------------------------------------------------------

@router.get("/reports/service-orders.pdf")
@router.get("/service-orders/pdf/export")
def export_service_orders_pdf(
    period: Optional[str] = Query(None, description="today | week | month | all"),
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Exporta serviços prestados filtrados como PDF profissional (ReportLab).
    Parâmetro `period` aceita: today, week, month, all.
    Em alternativa, pode fornecer start_date/end_date directamente.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.colors import HexColor

    terminal = controller.get_terminal_required(db, current_user.id)
    now = datetime.utcnow()

    # ── Determinar intervalo de datas ──────────────────────────────────────
    period_label_str = "Todos os Registos"

    if period == "today":
        start_date = datetime(now.year, now.month, now.day, 0, 0, 0)
        end_date = datetime(now.year, now.month, now.day, 23, 59, 59)
        period_label_str = f"Hoje — {now.strftime('%d/%m/%Y')}"

    elif period == "week":
        weekday = now.weekday()  # 0=Mon
        start_date = datetime(now.year, now.month, now.day) - timedelta(days=weekday)
        end_date = start_date + timedelta(days=6, hours=23, minutes=59, seconds=59)
        period_label_str = f"Esta Semana ({start_date.strftime('%d/%m')} – {end_date.strftime('%d/%m/%Y')})"

    elif period == "month":
        start_date = datetime(now.year, now.month, 1)
        import calendar
        last_day = calendar.monthrange(now.year, now.month)[1]
        end_date = datetime(now.year, now.month, last_day, 23, 59, 59)
        period_label_str = f"Este Mês — {now.strftime('%B %Y')}"

    else:
        # all or custom range
        if start_date and end_date:
            period_label_str = f"{start_date.strftime('%d/%m/%Y')} até {end_date.strftime('%d/%m/%Y')}"

    # ── Buscar ordens ───────────────────────────────────────────────────────
    from models import PDVServiceOrder
    from sqlalchemy import desc as sa_desc

    query = (
        db.query(PDVServiceOrder)
        .filter(PDVServiceOrder.terminal_id == terminal.id)
        .order_by(sa_desc(PDVServiceOrder.created_at))
    )
    if start_date:
        query = query.filter(PDVServiceOrder.created_at >= start_date)
    if end_date:
        query = query.filter(PDVServiceOrder.created_at <= end_date)

    orders = query.all()

    # ── Calcular totais ─────────────────────────────────────────────────────
    total_revenue = sum(float(o.total) for o in orders)
    total_count = len(orders)
    avg_value = total_revenue / total_count if total_count > 0 else 0.0

    currency = terminal.currency or "MT"

    def fmt_money(v) -> str:
        try:
            return f"{float(v):,.2f} {currency}"
        except Exception:
            return f"0.00 {currency}"

    def fmt_qty(v) -> str:
        try:
            f = float(v)
            return str(int(f)) if f == int(f) else f"{f:.2f}"
        except Exception:
            return str(v)

    def fmt_dt(dt) -> str:
        local_dt = controller.to_mozambique_datetime(dt)
        if not local_dt:
            return ""
        return local_dt.strftime("%d/%m/%Y %H:%M")

    # ── Mapeamento de método de pagamento ───────────────────────────────────
    PAY_LABELS = {
        "cash": "Dinheiro",
        "card": "Cartão",
        "mpesa": "M-Pesa",
        "skywallet": "SkyWallet",
        "emola": "e-Mola",
        "mixed": "Misto",
        "ponto24": "Ponto 24",
        "multicaixa": "Multicaixa",
    }

    def pay_label(method: str) -> str:
        return PAY_LABELS.get(str(method).lower(), str(method))

    # ── Cores da marca ─────────────────────────────────────────────────────
    PRIMARY = HexColor("#4F46E5")      # indigo-600
    PRIMARY_LIGHT = HexColor("#EEF2FF")
    EMERALD = HexColor("#059669")
    GRAY_DARK = HexColor("#111827")
    GRAY_MID = HexColor("#6B7280")
    GRAY_LIGHT = HexColor("#F9FAFB")
    BORDER = HexColor("#E5E7EB")
    WHITE = colors.white

    # ── Estilos de texto ───────────────────────────────────────────────────
    styles = getSampleStyleSheet()
    style_title = ParagraphStyle(
        "SkyTitle",
        parent=styles["Normal"],
        fontSize=20,
        fontName="Helvetica-Bold",
        textColor=GRAY_DARK,
        spaceAfter=2,
    )
    style_subtitle = ParagraphStyle(
        "SkySub",
        parent=styles["Normal"],
        fontSize=9,
        fontName="Helvetica",
        textColor=GRAY_MID,
        spaceAfter=0,
    )
    style_section = ParagraphStyle(
        "SkySection",
        parent=styles["Normal"],
        fontSize=11,
        fontName="Helvetica-Bold",
        textColor=GRAY_DARK,
        spaceBefore=14,
        spaceAfter=4,
    )
    style_normal = ParagraphStyle(
        "SkyNormal",
        parent=styles["Normal"],
        fontSize=8,
        fontName="Helvetica",
        textColor=GRAY_DARK,
    )
    style_small = ParagraphStyle(
        "SkySmall",
        parent=styles["Normal"],
        fontSize=7,
        fontName="Helvetica",
        textColor=GRAY_MID,
    )
    style_bold = ParagraphStyle(
        "SkyBold",
        parent=styles["Normal"],
        fontSize=8,
        fontName="Helvetica-Bold",
        textColor=GRAY_DARK,
    )
    style_money = ParagraphStyle(
        "SkyMoney",
        parent=styles["Normal"],
        fontSize=8,
        fontName="Helvetica-Bold",
        textColor=EMERALD,
        alignment=TA_RIGHT,
    )
    style_header_cell = ParagraphStyle(
        "SkyHeaderCell",
        parent=styles["Normal"],
        fontSize=8,
        fontName="Helvetica-Bold",
        textColor=WHITE,
    )

    # ── Construir PDF ──────────────────────────────────────────────────────
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=28,
        rightMargin=28,
        topMargin=28,
        bottomMargin=28,
    )
    story = []
    page_w = A4[0] - 56  # usable width

    # --- Dados da Empresa/Terminal (dinâmicos por conta/terminal) ---
    t_settings = terminal.settings if isinstance(terminal.settings, dict) else {}
    company_name = (
        t_settings.get("receipt_company_name")
        or terminal.name
        or "SkyPDV"
    )
    company_address = (
        t_settings.get("receipt_address")
        or terminal.address
        or ""
    )
    company_contacts = (
        t_settings.get("receipt_contacts")
        or terminal.phone
        or ""
    )
    company_nuit = t_settings.get("receipt_nuit") or ""

    # --- Cabeçalho ---
    story.append(Paragraph(company_name, style_title))
    if company_address:
        story.append(Paragraph(company_address, style_subtitle))
    if company_contacts or company_nuit:
        extra_info = []
        if company_contacts:
            extra_info.append(f"Contacto: {company_contacts}")
        if company_nuit:
            extra_info.append(f"NUIT: {company_nuit}")
        story.append(Paragraph(" · ".join(extra_info), style_subtitle))
    story.append(Spacer(1, 4))
    story.append(Paragraph("Relatório de Serviços Prestados", style_subtitle))
    story.append(Paragraph(f"Período: {period_label_str}", style_subtitle))
    story.append(Paragraph(f"Emitido em: {fmt_dt(now)}", style_subtitle))
    story.append(Spacer(1, 10))

    # --- Linha divisória ---
    story.append(Table(
        [[""]],
        colWidths=[page_w],
        style=TableStyle([
            ("LINEABOVE", (0, 0), (-1, 0), 1.5, PRIMARY),
        ]),
    ))
    story.append(Spacer(1, 8))

    # --- Cartões de resumo (3 colunas) ---
    col_w = page_w / 3.0

    cards_table = Table(
        [
            [
                Paragraph("Receita Total", style_small),
                Paragraph("Prestações", style_small),
                Paragraph("Preço Médio", style_small),
            ],
            [
                Paragraph(fmt_money(total_revenue), ParagraphStyle("rev", parent=style_bold, fontSize=12, textColor=EMERALD)),
                Paragraph(str(total_count), ParagraphStyle("cnt", parent=style_bold, fontSize=12, textColor=PRIMARY)),
                Paragraph(fmt_money(avg_value), ParagraphStyle("avg", parent=style_bold, fontSize=12, textColor=GRAY_DARK)),
            ],
        ],
        colWidths=[col_w, col_w, col_w],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), GRAY_LIGHT),
            ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER),
            ("TOPPADDING", (0, 0), (-1, 0), 6),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
            ("TOPPADDING", (0, 1), (-1, 1), 2),
            ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]),
    )
    story.append(cards_table)
    story.append(Spacer(1, 14))

    # --- Tabela de prestações ---
    story.append(Paragraph(f"Detalhes das Prestações ({total_count} registos)", style_section))

    if not orders:
        story.append(Paragraph("Nenhuma prestação encontrada para este período.", style_normal))
    else:
        col_widths = [
            page_w * 0.10,   # Recibo
            page_w * 0.14,   # Data
            page_w * 0.26,   # Serviço
            page_w * 0.06,   # Qtd
            page_w * 0.18,   # Cliente
            page_w * 0.11,   # Pagamento
            page_w * 0.15,   # Total
        ]

        header_row = [
            Paragraph("Recibo", style_header_cell),
            Paragraph("Data / Hora", style_header_cell),
            Paragraph("Serviço", style_header_cell),
            Paragraph("Qtd", style_header_cell),
            Paragraph("Cliente", style_header_cell),
            Paragraph("Pagamento", style_header_cell),
            Paragraph("Total", ParagraphStyle("RH", parent=style_header_cell, alignment=TA_RIGHT)),
        ]

        data_rows = [header_row]
        for i, o in enumerate(orders):
            service_text = str(o.service_name or "")
            if o.notes:
                service_text += f"<br/>{o.notes}"
            customer_text = str(o.customer_name or "Balcão")
            if o.customer_phone:
                customer_text += f"<br/>{o.customer_phone}"

            data_rows.append([
                Paragraph(str(o.receipt_number or f"#{o.id}"), style_small),
                Paragraph(fmt_dt(o.created_at), style_small),
                Paragraph(service_text, style_normal),
                Paragraph(fmt_qty(o.quantity), style_normal),
                Paragraph(customer_text, style_small),
                Paragraph(pay_label(o.payment_method or ""), style_small),
                Paragraph(fmt_money(o.total), style_money),
            ])

        # Total row
        data_rows.append([
            Paragraph("", style_bold),
            Paragraph("", style_bold),
            Paragraph("", style_bold),
            Paragraph("", style_bold),
            Paragraph("", style_bold),
            Paragraph("TOTAL", ParagraphStyle("TL", parent=style_bold, alignment=TA_RIGHT)),
            Paragraph(fmt_money(total_revenue), ParagraphStyle("TR", parent=style_money, fontSize=9)),
        ])

        n_data = len(data_rows)
        table_style = TableStyle([
            # Header
            ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
            ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("TOPPADDING", (0, 0), (-1, 0), 7),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
            # Body alternating
            ("FONTSIZE", (0, 1), (-1, -2), 7),
            ("TOPPADDING", (0, 1), (-1, -2), 5),
            ("BOTTOMPADDING", (0, 1), (-1, -2), 5),
            # Total row
            ("BACKGROUND", (0, n_data - 1), (-1, n_data - 1), PRIMARY_LIGHT),
            ("FONTNAME", (0, n_data - 1), (-1, n_data - 1), "Helvetica-Bold"),
            ("TOPPADDING", (0, n_data - 1), (-1, n_data - 1), 7),
            ("BOTTOMPADDING", (0, n_data - 1), (-1, n_data - 1), 7),
            ("LINEABOVE", (0, n_data - 1), (-1, n_data - 1), 1, PRIMARY),
            # Grid
            ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
            ("ROWBACKGROUNDS", (0, 1), (-1, n_data - 2), [WHITE, GRAY_LIGHT]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ])

        t = Table(data_rows, colWidths=col_widths, repeatRows=1, style=table_style)
        story.append(t)

    # --- Rodapé ---
    story.append(Spacer(1, 16))
    story.append(Table(
        [[""]],
        colWidths=[page_w],
        style=TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.5, BORDER)]),
    ))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"SkyPDV · Gerado em {fmt_dt(now)} · {company_name}",
        ParagraphStyle("footer", parent=style_small, alignment=TA_CENTER),
    ))

    doc.build(story)
    buffer.seek(0)

    safe_period = (period or "todos").replace(" ", "_")
    filename = f"servicos_{safe_period}_{now.strftime('%Y%m%d_%H%M')}.pdf"

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Access-Control-Expose-Headers": "Content-Disposition",
    }
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers=headers,
    )
