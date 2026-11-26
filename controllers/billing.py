"""Cobrança SkyPDV via SkyWallet Developer API (payment links) + transferência M-Pesa."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import urllib.error
import urllib.request
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from models import PDVBillingInvoice, PDVTerminal, User

logger = logging.getLogger(__name__)

SKYWALLET_URL = os.getenv("SKYWALLET_URL", "https://skywallet-api.bluesparkmz.com").rstrip("/")
SKYWALLET_SK_ID = os.getenv("SKYWALLET_SK_ID", "").strip()
SKYWALLET_API_KEY = os.getenv("SKYWALLET_API_KEY", "").strip()
SKYWALLET_WEBHOOK_SECRET = os.getenv("SKYWALLET_WEBHOOK_SECRET", "").strip()

TRANSFER_NUMBERS = ("860716912", "852411827")

PLAN_PRICES_MZN = {
    "basic": Decimal(os.getenv("SKYPDV_PLAN_BASIC_MZN", "1300")),
    "pro": Decimal(os.getenv("SKYPDV_PLAN_PRO_MZN", "1950")),
}


def skywallet_enabled() -> bool:
    return bool(SKYWALLET_SK_ID and SKYWALLET_API_KEY)


def transfer_instructions() -> dict:
    return {
        "method": "mpesa_transfer",
        "numbers": list(TRANSFER_NUMBERS),
        "label": "Transferência M-Pesa",
        "hint": "Transfere o valor e envia o comprovativo. No manager marca como pago.",
    }


def _new_order_id() -> str:
    return f"SKPDV-{secrets.token_hex(5).upper()}"


def _skywallet_request(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{SKYWALLET_URL}{path}"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-SK-ID": SKYWALLET_SK_ID,
        "X-API-Key": SKYWALLET_API_KEY,
        "User-Agent": "SkyPDV/1.0",
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (ValueError, OSError):
            payload = {}
        detail = payload.get("detail") or payload.get("message") or f"SkyWallet recusou ({exc.code})"
        raise HTTPException(status_code=502, detail=str(detail)) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise HTTPException(status_code=503, detail="SkyWallet indisponível, tenta outra vez") from exc
    return json.loads(raw) if raw else {}


def _resolve_amount(plan: str, months: int, amount_mzn: Decimal | None) -> Decimal:
    if amount_mzn is not None:
        value = Decimal(str(amount_mzn)).quantize(Decimal("0.01"))
        if value <= 0:
            raise HTTPException(status_code=400, detail="Valor inválido")
        return value
    unit = PLAN_PRICES_MZN.get(plan)
    if unit is None:
        raise HTTPException(status_code=400, detail="Plano inválido")
    return (unit * Decimal(months)).quantize(Decimal("0.01"))


def billing_end_date(months: int = 1, from_dt: datetime | None = None) -> datetime:
    """Validade: dia 1 do mês seguinte (ou N meses à frente)."""
    from_dt = from_dt or datetime.utcnow()
    months = max(1, int(months or 1))
    year = from_dt.year
    month = from_dt.month + months
    while month > 12:
        month -= 12
        year += 1
    return datetime(year, month, 1)


def get_billing_children(db: Session, parent_terminal_id: int) -> list[PDVTerminal]:
    return (
        db.query(PDVTerminal)
        .filter(PDVTerminal.billing_parent_terminal_id == parent_terminal_id)
        .order_by(PDVTerminal.id.asc())
        .all()
    )


def get_billing_group(db: Session, terminal: PDVTerminal) -> list[PDVTerminal]:
    """Empresa principal + lojas associadas (ou só a própria se for independente)."""
    if terminal.billing_parent_terminal_id:
        parent = (
            db.query(PDVTerminal)
            .filter(PDVTerminal.id == terminal.billing_parent_terminal_id)
            .first()
        )
        if parent:
            return [parent] + get_billing_children(db, parent.id)
    return [terminal] + get_billing_children(db, terminal.id)


def set_billing_parent(
    db: Session,
    *,
    terminal_id: int,
    billing_parent_terminal_id: int | None,
) -> PDVTerminal:
    terminal = db.query(PDVTerminal).filter(PDVTerminal.id == terminal_id).first()
    if not terminal:
        raise HTTPException(status_code=404, detail="Terminal não encontrado")

    if billing_parent_terminal_id is None:
        terminal.billing_parent_terminal_id = None
        terminal.updated_at = datetime.utcnow()
        db.add(terminal)
        db.commit()
        db.refresh(terminal)
        return terminal

    if billing_parent_terminal_id == terminal_id:
        raise HTTPException(status_code=400, detail="Uma loja não pode ser conta principal de si mesma")

    parent = db.query(PDVTerminal).filter(PDVTerminal.id == billing_parent_terminal_id).first()
    if not parent:
        raise HTTPException(status_code=404, detail="Conta principal não encontrada")
    if parent.billing_parent_terminal_id is not None:
        raise HTTPException(
            status_code=400,
            detail="A conta principal não pode ser uma loja já associada a outra",
        )
    # Evitar ciclos: se A já tem filhas, não pode passar a ser filha de B
    if get_billing_children(db, terminal.id):
        raise HTTPException(
            status_code=400,
            detail="Esta loja já é conta principal de outras. Remove as associações primeiro",
        )

    terminal.billing_parent_terminal_id = parent.id
    terminal.updated_at = datetime.utcnow()
    db.add(terminal)
    db.commit()
    db.refresh(terminal)
    return terminal


def serialize_invoice(invoice: PDVBillingInvoice) -> dict:
    return {
        "id": invoice.id,
        "terminal_id": invoice.terminal_id,
        "plan": invoice.plan,
        "months": invoice.months,
        "amount": float(invoice.amount),
        "currency": invoice.currency,
        "status": invoice.status,
        "order_id": invoice.order_id,
        "payment_url": invoice.payment_url,
        "payment_method": invoice.payment_method,
        "notes": invoice.notes,
        "paid_at": invoice.paid_at.isoformat() if invoice.paid_at else None,
        "created_at": invoice.created_at.isoformat() if invoice.created_at else None,
        "transfer": transfer_instructions(),
        "skywallet_enabled": skywallet_enabled(),
    }


def create_invoice(
    db: Session,
    *,
    terminal_id: int,
    admin: User,
    plan: str = "basic",
    months: int = 1,
    amount_mzn: Decimal | None = None,
    notes: str | None = None,
) -> dict:
    plan = (plan or "basic").strip().lower()
    if plan not in PLAN_PRICES_MZN:
        raise HTTPException(status_code=400, detail="Plano inválido (basic|pro)")
    months = max(1, min(int(months or 1), 24))

    terminal = (
        db.query(PDVTerminal)
        .options(joinedload(PDVTerminal.user))
        .filter(PDVTerminal.id == terminal_id)
        .first()
    )
    if not terminal:
        raise HTTPException(status_code=404, detail="Terminal não encontrado")

    # Cobrança só na conta principal
    if terminal.billing_parent_terminal_id:
        parent = (
            db.query(PDVTerminal)
            .filter(PDVTerminal.id == terminal.billing_parent_terminal_id)
            .first()
        )
        parent_name = parent.name if parent else f"#{terminal.billing_parent_terminal_id}"
        raise HTTPException(
            status_code=400,
            detail=(
                f"Esta loja é cobrada na conta principal «{parent_name}». "
                f"Cria a fatura no terminal #{terminal.billing_parent_terminal_id}."
            ),
        )

    children = get_billing_children(db, terminal.id)
    group_size = 1 + len(children)
    if amount_mzn is not None:
        amount = _resolve_amount(plan, months, amount_mzn)
    else:
        unit = PLAN_PRICES_MZN.get(plan)
        if unit is None:
            raise HTTPException(status_code=400, detail="Plano inválido")
        amount = (unit * Decimal(months) * Decimal(group_size)).quantize(Decimal("0.01"))

    child_names = ", ".join(child.name for child in children)
    order_id = _new_order_id()
    if children:
        description = (
            f"SkyPDV {plan.title()} · {months} mês(es) · {terminal.name} "
            f"+ {len(children)} associada(s)"
            + (f" ({child_names})" if child_names else "")
        )
        group_note = f"Grupo: {terminal.name}" + (f" + {child_names}" if child_names else "")
        notes = f"{notes.strip()} | {group_note}" if notes and notes.strip() else group_note
    else:
        description = f"SkyPDV {plan.title()} · {months} mês(es) · {terminal.name}"

    invoice = PDVBillingInvoice(
        terminal_id=terminal.id,
        created_by_user_id=admin.id,
        plan=plan,
        months=months,
        amount=amount,
        currency="MZN",
        status="pending",
        order_id=order_id,
        notes=(notes or "").strip() or None,
        payment_method="pending",
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    if not skywallet_enabled():
        invoice.status = "failed"
        invoice.payment_method = "transfer"
        invoice.notes = (
            (invoice.notes + " | " if invoice.notes else "")
            + "SkyWallet não configurado (SKYWALLET_SK_ID / SKYWALLET_API_KEY)"
        )
        db.add(invoice)
        db.commit()
        raise HTTPException(
            status_code=503,
            detail="SkyWallet não configurado. Define SKYWALLET_SK_ID e SKYWALLET_API_KEY no .env",
        )

    try:
        result = _skywallet_request(
            "POST",
            "/api/developer/payment-links",
            {
                "amount": float(amount),
                "currency": "MZN",
                "description": description,
                "order_id": order_id,
                "expires_in_hours": 72,
            },
        )
    except HTTPException as exc:
        invoice.status = "failed"
        invoice.payment_method = "transfer"
        invoice.notes = (
            (invoice.notes + " | " if invoice.notes else "")
            + f"Link SkyWallet falhou: {exc.detail}"
        )
        db.add(invoice)
        db.commit()
        raise

    link = result.get("payment_link") or {}
    payment_url = (link.get("payment_url") or "").strip()
    skywallet_link_id = link.get("id")
    if not payment_url:
        invoice.status = "failed"
        invoice.payment_method = "transfer"
        invoice.notes = (
            (invoice.notes + " | " if invoice.notes else "")
            + "SkyWallet não devolveu payment_url"
        )
        db.add(invoice)
        db.commit()
        raise HTTPException(status_code=502, detail="SkyWallet não devolveu o link de pagamento")

    invoice.payment_url = payment_url
    invoice.skywallet_link_id = int(skywallet_link_id) if skywallet_link_id else None
    invoice.payment_method = "skywallet_link"
    invoice.status = "pending"
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    valid_until = billing_end_date(invoice.months)
    payload = serialize_invoice(invoice)
    payload["terminal_name"] = terminal.name
    payload["billing_group_size"] = group_size
    payload["billing_child_names"] = [child.name for child in children]
    payload["valid_until"] = valid_until.isoformat()
    # E-mail HTML sempre para a conta principal (esta loja)
    owner_email = terminal.user.email if terminal.user else None
    payload["owner_email"] = owner_email

    email_sent = False
    if owner_email:
        try:
            from mail_service import send_invoice_created

            email_sent = send_invoice_created(
                to_email=owner_email,
                terminal_name=terminal.name,
                order_id=invoice.order_id,
                plan=invoice.plan,
                months=invoice.months,
                amount=float(invoice.amount),
                currency=invoice.currency,
                payment_url=invoice.payment_url,
                created_at=invoice.created_at,
                valid_until=valid_until,
                billing_child_names=[child.name for child in children],
            )
        except Exception:
            logger.exception("Erro inesperado ao enviar e-mail da fatura %s", invoice.order_id)
    payload["email_sent"] = email_sent
    return payload


def list_invoices(
    db: Session,
    *,
    terminal_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    q = db.query(PDVBillingInvoice).options(joinedload(PDVBillingInvoice.terminal))
    if terminal_id is not None:
        q = q.filter(PDVBillingInvoice.terminal_id == terminal_id)
    if status:
        q = q.filter(PDVBillingInvoice.status == status.strip().lower())
    total = q.order_by(None).count()
    rows = q.order_by(PDVBillingInvoice.created_at.desc()).offset(offset).limit(limit).all()
    items = []
    for invoice in rows:
        item = serialize_invoice(invoice)
        item["terminal_name"] = invoice.terminal.name if invoice.terminal else None
        items.append(item)
    return {"total": total, "limit": limit, "offset": offset, "items": items}


def _activate_one_terminal(db: Session, terminal: PDVTerminal, months: int, now: datetime) -> None:
    # Sempre dia 1 do mês seguinte (ou N meses à frente), a partir de agora
    # ou da data de cobrança actual se ainda estiver no futuro.
    base = now
    if terminal.next_billing_date and terminal.next_billing_date > now:
        base = terminal.next_billing_date
    terminal.next_billing_date = billing_end_date(months, base)
    terminal.subscription_status = "active"
    terminal.grace_period_ends_at = None
    terminal.updated_at = now
    db.add(terminal)


def _activate_terminal_for_invoice(db: Session, invoice: PDVBillingInvoice) -> None:
    terminal = db.query(PDVTerminal).filter(PDVTerminal.id == invoice.terminal_id).with_for_update().first()
    if not terminal:
        return
    now = datetime.utcnow()
    months = max(1, invoice.months)
    _activate_one_terminal(db, terminal, months, now)
    # Activa também as lojas associadas a esta conta principal
    for child in get_billing_children(db, terminal.id):
        locked = (
            db.query(PDVTerminal)
            .filter(PDVTerminal.id == child.id)
            .with_for_update()
            .first()
        )
        if locked:
            _activate_one_terminal(db, locked, months, now)


def mark_invoice_paid(
    db: Session,
    invoice_id: int,
    *,
    payment_method: str = "transfer",
    notes: str | None = None,
) -> dict:
    invoice = db.query(PDVBillingInvoice).filter(PDVBillingInvoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Fatura não encontrada")
    if invoice.status == "paid":
        return serialize_invoice(invoice)

    invoice.status = "paid"
    invoice.paid_at = datetime.utcnow()
    invoice.payment_method = payment_method
    if notes:
        invoice.notes = ((invoice.notes + " | ") if invoice.notes else "") + notes.strip()
    invoice.updated_at = datetime.utcnow()
    db.add(invoice)
    _activate_terminal_for_invoice(db, invoice)
    db.commit()
    db.refresh(invoice)
    return serialize_invoice(invoice)


def _verify_skywallet_signature(raw_body: bytes, signature: str | None) -> None:
    secret = SKYWALLET_WEBHOOK_SECRET
    if not secret:
        logger.warning("SKYWALLET_WEBHOOK_SECRET vazio — a aceitar webhook sem verificação")
        return
    if not signature:
        raise HTTPException(status_code=401, detail="Assinatura do webhook em falta")
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if hmac.compare_digest(expected, signature.strip()):
        return
    try:
        payload = json.loads(raw_body.decode("utf-8"))
        compact = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        expected_compact = hmac.new(secret.encode("utf-8"), compact, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected_compact, signature.strip()):
            return
    except (ValueError, UnicodeDecodeError):
        pass
    raise HTTPException(status_code=401, detail="Assinatura do webhook inválida")


def handle_skywallet_webhook(db: Session, raw_body: bytes, signature: str | None) -> dict:
    _verify_skywallet_signature(raw_body, signature)
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="JSON inválido") from exc

    order_id = str(payload.get("order_id") or "").strip()
    status = str(payload.get("status") or "").strip().lower()
    if not order_id:
        raise HTTPException(status_code=400, detail="order_id em falta")

    invoice = db.query(PDVBillingInvoice).filter(PDVBillingInvoice.order_id == order_id).first()
    if not invoice:
        logger.warning("Webhook SkyWallet para order_id desconhecido: %s", order_id)
        return {"ok": True, "matched": False}

    if status in {"success", "paid", "completed"}:
        mark_invoice_paid(db, invoice.id, payment_method="skywallet_link")
        return {"ok": True, "matched": True, "status": "paid"}

    if status in {"failed", "cancelled", "canceled"}:
        if invoice.status == "pending":
            invoice.status = "failed"
            invoice.updated_at = datetime.utcnow()
            db.add(invoice)
            db.commit()
        return {"ok": True, "matched": True, "status": invoice.status}

    return {"ok": True, "matched": True, "status": invoice.status}
