import csv
import io
from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from sqlalchemy.orm import Session
from sqlalchemy import func

from auth import get_current_user
from controllers import controller
from database import get_db
from models import PDVProduct, User
import schemas


router = APIRouter(
    prefix="/skypdv",
    tags=["skypdv-categories"],
)


def _fmt_money(value) -> str:
    try:
        return f"{float(value or 0):,.2f} MT"
    except Exception:
        return "0.00 MT"


def _fmt_qty(value) -> str:
    try:
        amount = float(value or 0)
        if amount == int(amount):
            return f"{int(amount):,}"
        return f"{amount:,.2f}"
    except Exception:
        return "0"


def _fmt_product_qty(product: PDVProduct, quantity: Decimal) -> str:
    """Mostra quantidades de produtos por peso em quilogramas no relatório."""
    if not getattr(product, "allow_decimal_quantity", False):
        return _fmt_qty(quantity)

    try:
        amount = Decimal(str(quantity or 0))
        formatted = format(amount, "f").rstrip("0").rstrip(".")
        return f"{formatted or '0'} Kg"
    except Exception:
        return "0kg"


def _available_products_by_category(db: Session, terminal_id: int, category: Optional[str] = None):
    query = (
        db.query(PDVProduct)
        .filter(
            PDVProduct.terminal_id == terminal_id,
            PDVProduct.is_active == True,
        )
    )

    if category:
        category_clean = category.strip()
        query = query.filter(func.lower(func.trim(PDVProduct.category)) == category_clean.lower())

    products = query.order_by(PDVProduct.category.asc(), PDVProduct.name.asc()).all()

    grouped: dict[str, list[PDVProduct]] = {}
    for product in products:
        category = str(product.category or "").strip() or "Sem categoria"
        grouped.setdefault(category, []).append(product)

    return dict(sorted(grouped.items(), key=lambda item: item[0].lower()))


def _product_qty(product: PDVProduct) -> Decimal:
    inventory = getattr(product, "inventory", None)
    try:
        return Decimal(str(getattr(inventory, "quantity", 0) or 0))
    except Exception:
        return Decimal("0.00")


def _product_price(product: PDVProduct) -> Decimal:
    try:
        return Decimal(str(getattr(product, "price", 0) or 0))
    except Exception:
        return Decimal("0.00")


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


@router.get("/categories/products.pdf")
def download_category_products_pdf(
    category: Optional[str] = Query(None, description="Nome da categoria para imprimir"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Baixar PDF com produtos disponiveis agrupados por categoria."""
    terminal = controller.get_terminal_required(db, current_user.id)
    grouped = _available_products_by_category(db, terminal.id, category)
    issued_at = datetime.utcnow()
    report_title = f"Produtos da categoria: {category.strip()}" if category and category.strip() else "Produtos por categoria"

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    story = [
        Paragraph(escape(report_title), styles["Title"]),
        Paragraph(f"Emitido em: {issued_at.strftime('%d/%m/%Y %H:%M')} (UTC)", styles["Normal"]),
        Spacer(1, 12),
    ]

    grand_products = 0
    grand_value = Decimal("0.00")

    if not grouped:
        story.append(Paragraph("Nenhum produto encontrado.", styles["Normal"]))

    for category_name, products in grouped.items():
        story.append(Paragraph(escape(category_name), styles["Heading2"]))

        table_data = [["Produto", "Qtd disponivel", "Preco"]]
        category_products = 0
        category_value = Decimal("0.00")

        for product in products:
            qty = _product_qty(product)
            price = _product_price(product)
            category_products += 1
            category_value += qty * price

            table_data.append([
                escape(str(product.name or "")),
                _fmt_product_qty(product, qty),
                _fmt_money(price),
            ])

        grand_products += category_products
        grand_value += category_value
        table_data.append(["TOTAL DA CATEGORIA", f"{category_products} produto(s)", _fmt_money(category_value)])

        table = Table(table_data, colWidths=[295, 105, 115], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                    ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                    ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F1F5F9")),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(1, 12))

    summary = Table(
        [["Total geral de produtos", f"{grand_products} produto(s)"], ["Valor geral disponivel", _fmt_money(grand_value)]],
        colWidths=[260, 255],
    )
    summary.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("TEXTCOLOR", (0, 1), (1, 1), colors.HexColor("#166534")),
            ]
        )
    )
    story.append(summary)

    doc.build(story)
    suffix = category.strip().lower().replace(" ", "_") if category and category.strip() else "todas"
    filename = f"produtos_categoria_{suffix}_{issued_at.strftime('%Y-%m-%d')}.pdf"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Access-Control-Expose-Headers": "Content-Disposition",
    }
    return StreamingResponse(io.BytesIO(buffer.getvalue()), media_type="application/pdf", headers=headers)


@router.get("/categories/products.csv")
def download_category_products_csv(
    category: Optional[str] = Query(None, description="Nome da categoria para exportar"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Baixar CSV com produtos disponiveis agrupados por categoria."""
    terminal = controller.get_terminal_required(db, current_user.id)
    grouped = _available_products_by_category(db, terminal.id, category)
    issued_at = datetime.utcnow()

    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer, delimiter=";")
    writer.writerow(["Categoria", "Produto", "Unidade", "Quantidade disponivel", "Preco unitario"])

    for category_name, products in grouped.items():
        for product in products:
            qty = _product_qty(product)
            price = _product_price(product)
            writer.writerow([
                category_name,
                product.name or "",
                "Kg" if getattr(product, "allow_decimal_quantity", False) else "Un.",
                _fmt_product_qty(product, qty),
                str(price),
            ])

    output = io.BytesIO(csv_buffer.getvalue().encode("utf-8-sig"))
    suffix = category.strip().lower().replace(" ", "_") if category and category.strip() else "todas"
    filename = f"produtos_categoria_{suffix}_{issued_at.strftime('%Y-%m-%d')}.csv"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Access-Control-Expose-Headers": "Content-Disposition",
    }
    return StreamingResponse(output, media_type="text/csv; charset=utf-8", headers=headers)


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
