"""Manager de plataforma SkyPDV — só admin BlueSpark."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from auth import get_current_platform_admin
from database import get_db
from models import PDVSale, PDVTerminal, PDVTerminalUser, User

router = APIRouter(prefix="/manager", tags=["manager"])


class ManagerOverview(BaseModel):
    total_terminals: int
    active_terminals: int
    inactive_terminals: int
    trial: int
    active_subs: int
    suspended: int
    total_users: int


class ManagerTerminalOwner(BaseModel):
    id: int
    email: str
    username: str
    name: str | None = None
    phone: str | None = None
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class ManagerTerminalItem(BaseModel):
    id: int
    name: str
    active: bool
    subscription_status: str
    next_billing_date: datetime | None = None
    grace_period_ends_at: datetime | None = None
    created_at: datetime | None = None
    staff_count: int = 0
    sales_count: int = 0
    owner: ManagerTerminalOwner


class ManagerTerminalsPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[ManagerTerminalItem]


class ManagerTerminalUpdate(BaseModel):
    active: bool | None = None
    subscription_status: str | None = None
    next_billing_date: datetime | None = None
    grace_period_ends_at: datetime | None = None


def _count_terminals(db: Session, *filters) -> int:
    q = db.query(func.count(PDVTerminal.id))
    for item in filters:
        q = q.filter(item)
    return int(q.scalar() or 0)


def _terminal_item(db: Session, terminal: PDVTerminal) -> ManagerTerminalItem:
    staff_count = int(
        db.query(func.count(PDVTerminalUser.id))
        .filter(
            PDVTerminalUser.terminal_id == terminal.id,
            PDVTerminalUser.is_active.is_(True),
        )
        .scalar()
        or 0
    )
    sales_count = int(
        db.query(func.count(PDVSale.id)).filter(PDVSale.terminal_id == terminal.id).scalar() or 0
    )
    return ManagerTerminalItem(
        id=terminal.id,
        name=terminal.name,
        active=bool(terminal.active),
        subscription_status=terminal.subscription_status or "trial",
        next_billing_date=terminal.next_billing_date,
        grace_period_ends_at=terminal.grace_period_ends_at,
        created_at=terminal.created_at,
        staff_count=staff_count,
        sales_count=sales_count,
        owner=ManagerTerminalOwner.model_validate(terminal.user),
    )


@router.get("/overview", response_model=ManagerOverview)
def manager_overview(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_platform_admin),
):
    total_terminals = _count_terminals(db)
    active_terminals = _count_terminals(db, PDVTerminal.active.is_(True))
    return ManagerOverview(
        total_terminals=total_terminals,
        active_terminals=active_terminals,
        inactive_terminals=max(0, total_terminals - active_terminals),
        trial=_count_terminals(db, PDVTerminal.subscription_status == "trial"),
        active_subs=_count_terminals(db, PDVTerminal.subscription_status == "active"),
        suspended=_count_terminals(db, PDVTerminal.subscription_status == "suspended"),
        total_users=int(db.query(func.count(User.id)).scalar() or 0),
    )


@router.get("/terminals", response_model=ManagerTerminalsPage)
def manager_list_terminals(
    q: str | None = None,
    subscription_status: str | None = None,
    active: bool | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_platform_admin),
):
    stmt = db.query(PDVTerminal).options(joinedload(PDVTerminal.user))
    if q and q.strip():
        term = f"%{q.strip().lower()}%"
        stmt = stmt.join(User, User.id == PDVTerminal.user_id).filter(
            or_(
                func.lower(PDVTerminal.name).like(term),
                func.lower(User.email).like(term),
                func.lower(User.username).like(term),
                func.lower(func.coalesce(User.name, "")).like(term),
                func.lower(func.coalesce(User.phone, "")).like(term),
            )
        )
    if subscription_status:
        stmt = stmt.filter(PDVTerminal.subscription_status == subscription_status.strip().lower())
    if active is not None:
        stmt = stmt.filter(PDVTerminal.active.is_(active))

    total = stmt.order_by(None).count()
    rows = stmt.order_by(PDVTerminal.created_at.desc()).offset(offset).limit(limit).all()
    items = [_terminal_item(db, terminal) for terminal in rows if terminal.user]
    return ManagerTerminalsPage(total=total, limit=limit, offset=offset, items=items)


@router.patch("/terminals/{terminal_id}", response_model=ManagerTerminalItem)
def manager_update_terminal(
    terminal_id: int,
    payload: ManagerTerminalUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_platform_admin),
):
    terminal = (
        db.query(PDVTerminal)
        .options(joinedload(PDVTerminal.user))
        .filter(PDVTerminal.id == terminal_id)
        .first()
    )
    if not terminal or not terminal.user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Terminal não encontrado")

    if payload.active is not None:
        terminal.active = payload.active
    if payload.subscription_status is not None:
        status_value = payload.subscription_status.strip().lower()
        if status_value not in {"trial", "active", "suspended", "cancelled"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="subscription_status inválido",
            )
        terminal.subscription_status = status_value
    if payload.next_billing_date is not None:
        terminal.next_billing_date = payload.next_billing_date
    if payload.grace_period_ends_at is not None:
        terminal.grace_period_ends_at = payload.grace_period_ends_at

    terminal.updated_at = datetime.utcnow()
    db.add(terminal)
    db.commit()
    db.refresh(terminal)
    return _terminal_item(db, terminal)


class ManagerInvoiceCreate(BaseModel):
    plan: str = "basic"
    months: int = 1
    amount_mzn: float | None = None
    notes: str | None = None


class ManagerInvoiceMarkPaid(BaseModel):
    notes: str | None = None
    payment_method: str = "transfer"


@router.post("/terminals/{terminal_id}/invoices")
def manager_create_invoice(
    terminal_id: int,
    payload: ManagerInvoiceCreate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_platform_admin),
):
    from controllers import billing as billing_controller
    from decimal import Decimal

    amount = Decimal(str(payload.amount_mzn)) if payload.amount_mzn is not None else None
    return billing_controller.create_invoice(
        db,
        terminal_id=terminal_id,
        admin=current_admin,
        plan=payload.plan,
        months=payload.months,
        amount_mzn=amount,
        notes=payload.notes,
    )


@router.get("/terminals/{terminal_id}/invoices")
def manager_list_terminal_invoices(
    terminal_id: int,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_platform_admin),
):
    from controllers import billing as billing_controller

    return billing_controller.list_invoices(
        db,
        terminal_id=terminal_id,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.get("/invoices")
def manager_list_invoices(
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_platform_admin),
):
    from controllers import billing as billing_controller

    return billing_controller.list_invoices(
        db,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.post("/invoices/{invoice_id}/mark-paid")
def manager_mark_invoice_paid(
    invoice_id: int,
    payload: ManagerInvoiceMarkPaid,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_platform_admin),
):
    from controllers import billing as billing_controller

    return billing_controller.mark_invoice_paid(
        db,
        invoice_id,
        payment_method=payload.payment_method or "transfer",
        notes=payload.notes,
    )


@router.get("/billing/options")
def manager_billing_options(_: User = Depends(get_current_platform_admin)):
    from controllers import billing as billing_controller

    return {
        "plans": {
            "basic": {"label": "Básico", "amount_mzn": float(billing_controller.PLAN_PRICES_MZN["basic"])},
            "pro": {"label": "Pro", "amount_mzn": float(billing_controller.PLAN_PRICES_MZN["pro"])},
        },
        "transfer": billing_controller.transfer_instructions(),
        "skywallet_enabled": billing_controller.skywallet_enabled(),
    }
