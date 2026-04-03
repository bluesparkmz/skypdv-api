from datetime import datetime
import enum

from sqlalchemy import Boolean, Column, DateTime, DECIMAL, Enum, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from database import Base


# Shared compatibility note:
# SkyPDV still references shared "users" and cross-app external order ids during extraction.


class SourceType(str, enum.Enum):
    LOCAL = "local"
    FASTFOOD = "fastfood"
    SKYVENDA = "skyvenda"


class MovementType(str, enum.Enum):
    IN = "in"
    OUT = "out"
    ADJUSTMENT = "adjustment"
    SALE = "sale"
    RETURN = "return"
    TRANSFER = "transfer"


class PaymentMethod(str, enum.Enum):
    CASH = "cash"
    CARD = "card"
    SKYWALLET = "skywallet"
    MPESA = "mpesa"
    MIXED = "mixed"


class SaleType(str, enum.Enum):
    LOCAL = "local"
    DELIVERY = "delivery"
    ONLINE = "online"


class PDVTerminalRole(str, enum.Enum):
    ADMIN = "admin"
    CASHIER = "cashier"
    MANAGER = "manager"
    VIEWER = "viewer"


class PDVTerminal(Base):
    __tablename__ = "pdv_terminals"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    logo = Column(Text, nullable=True)
    bio = Column(String(500), nullable=True)
    active = Column(Boolean, default=True)
    settings = Column(JSON, nullable=True)
    tax_rate = Column(DECIMAL, default=0.00)
    currency = Column(String(10), default="MZN")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", backref="pdv_terminal")
    terminal_users = relationship("PDVTerminalUser", back_populates="terminal", cascade="all, delete-orphan")
    suppliers = relationship("PDVSupplier", back_populates="terminal", cascade="all, delete-orphan")
    products = relationship("PDVProduct", back_populates="terminal", cascade="all, delete-orphan")
    cash_registers = relationship("PDVCashRegister", back_populates="terminal", cascade="all, delete-orphan")
    sales = relationship("PDVSale", back_populates="terminal", cascade="all, delete-orphan")


class PDVTerminalUser(Base):
    __tablename__ = "pdv_terminal_users"

    id = Column(Integer, primary_key=True, index=True)
    terminal_id = Column(Integer, ForeignKey("pdv_terminals.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(Enum(PDVTerminalRole, values_callable=lambda x: [e.value for e in x], native_enum=False), nullable=False, default=PDVTerminalRole.CASHIER)
    can_sell = Column(Boolean, default=True)
    can_open_cash_register = Column(Boolean, default=True)
    can_manage_products = Column(Boolean, default=False)
    can_manage_stock = Column(Boolean, default=False)
    can_view_reports = Column(Boolean, default=True)
    can_manage_users = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    invited_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    invited_at = Column(DateTime, default=datetime.utcnow)
    joined_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    terminal = relationship("PDVTerminal", back_populates="terminal_users")
    user = relationship("User", foreign_keys=[user_id], backref="pdv_terminal_memberships")
    inviter = relationship("User", foreign_keys=[invited_by], backref="pdv_invitations_sent")

    __table_args__ = (UniqueConstraint("terminal_id", "user_id", name="uq_terminal_user"),)


class PDVSupplier(Base):
    __tablename__ = "pdv_suppliers"

    id = Column(Integer, primary_key=True, index=True)
    terminal_id = Column(Integer, ForeignKey("pdv_terminals.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    source_type = Column(Enum(SourceType), nullable=False, default=SourceType.LOCAL)
    external_id = Column(Integer, nullable=True)
    contact_name = Column(String(255), nullable=True)
    contact_phone = Column(String(50), nullable=True)
    contact_email = Column(String(255), nullable=True)
    address = Column(String(500), nullable=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    last_sync_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    terminal = relationship("PDVTerminal", back_populates="suppliers")
    products = relationship("PDVProduct", back_populates="supplier")


class PDVProduct(Base):
    __tablename__ = "pdv_products"

    id = Column(Integer, primary_key=True, index=True)
    terminal_id = Column(Integer, ForeignKey("pdv_terminals.id", ondelete="CASCADE"), nullable=False)
    supplier_id = Column(Integer, ForeignKey("pdv_suppliers.id", ondelete="SET NULL"), nullable=True)
    source_type = Column(Enum(SourceType), nullable=False, default=SourceType.LOCAL)
    external_product_id = Column(Integer, nullable=True)
    name = Column(String(255), nullable=False)
    sku = Column(String(100), nullable=True, index=True)
    barcode = Column(String(100), nullable=True, index=True)
    description = Column(Text, nullable=True)
    category = Column(String(100), nullable=True)
    cost_price = Column(DECIMAL, default=0.00)
    price = Column(DECIMAL, nullable=False)
    promotional_price = Column(DECIMAL, nullable=True)
    image = Column(Text, nullable=True)
    emoji = Column(String(10), nullable=True)
    is_active = Column(Boolean, default=True)
    is_fastfood = Column(Boolean, default=False)
    track_stock = Column(Boolean, default=True)
    allow_decimal_quantity = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    terminal = relationship("PDVTerminal", back_populates="products")
    supplier = relationship("PDVSupplier", back_populates="products")
    inventory = relationship("PDVInventory", back_populates="product", uselist=False, cascade="all, delete-orphan")
    stock_movements = relationship("PDVStockMovement", back_populates="product", cascade="all, delete-orphan")
    sale_items = relationship("PDVSaleItem", back_populates="product")


class PDVInventory(Base):
    __tablename__ = "pdv_inventory"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("pdv_products.id", ondelete="CASCADE"), nullable=False)
    terminal_id = Column(Integer, ForeignKey("pdv_terminals.id", ondelete="CASCADE"), nullable=False)
    storage_location = Column(String(50), default="balcao", index=True)
    quantity = Column(DECIMAL, default=0.00)
    min_quantity = Column(DECIMAL, default=0.00)
    max_quantity = Column(DECIMAL, nullable=True)
    reserved_quantity = Column(DECIMAL, default=0.00)
    last_restock_at = Column(DateTime, nullable=True)
    last_count_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    product = relationship("PDVProduct", back_populates="inventory")


class PDVStockMovement(Base):
    __tablename__ = "pdv_stock_movements"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("pdv_products.id", ondelete="CASCADE"), nullable=False)
    terminal_id = Column(Integer, ForeignKey("pdv_terminals.id", ondelete="CASCADE"), nullable=False)
    movement_type = Column(Enum(MovementType), nullable=False)
    quantity = Column(DECIMAL, nullable=False)
    quantity_before = Column(DECIMAL, nullable=True)
    quantity_after = Column(DECIMAL, nullable=True)
    reference = Column(String(255), nullable=True)
    reference_id = Column(Integer, nullable=True)
    from_location = Column(String(50), nullable=True)
    to_location = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    product = relationship("PDVProduct", back_populates="stock_movements")
    user = relationship("User", backref="pdv_stock_movements")


class PDVCashRegister(Base):
    __tablename__ = "pdv_cash_registers"

    id = Column(Integer, primary_key=True, index=True)
    terminal_id = Column(Integer, ForeignKey("pdv_terminals.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    opened_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)
    opening_amount = Column(DECIMAL, default=0.00)
    closing_amount = Column(DECIMAL, nullable=True)
    expected_amount = Column(DECIMAL, nullable=True)
    difference = Column(DECIMAL, nullable=True)
    total_cash = Column(DECIMAL, default=0.00)
    total_card = Column(DECIMAL, default=0.00)
    total_skywallet = Column(DECIMAL, default=0.00)
    total_mpesa = Column(DECIMAL, default=0.00)
    total_sales = Column(DECIMAL, default=0.00)
    total_refunds = Column(DECIMAL, default=0.00)
    sales_count = Column(Integer, default=0)
    refunds_count = Column(Integer, default=0)
    status = Column(String(20), default="open")
    notes = Column(Text, nullable=True)

    terminal = relationship("PDVTerminal", back_populates="cash_registers")
    user = relationship("User", backref="pdv_cash_registers")
    sales = relationship("PDVSale", back_populates="cash_register")


class PDVSale(Base):
    __tablename__ = "pdv_sales"

    id = Column(Integer, primary_key=True, index=True)
    terminal_id = Column(Integer, ForeignKey("pdv_terminals.id", ondelete="CASCADE"), nullable=False)
    cash_register_id = Column(Integer, ForeignKey("pdv_cash_registers.id", ondelete="SET NULL"), nullable=True)
    customer_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    customer_name = Column(String(255), nullable=True)
    customer_phone = Column(String(50), nullable=True)
    subtotal = Column(DECIMAL, nullable=False)
    discount_amount = Column(DECIMAL, default=0.00)
    discount_percent = Column(DECIMAL, default=0.00)
    tax_amount = Column(DECIMAL, default=0.00)
    total = Column(DECIMAL, nullable=False)
    payment_method = Column(Enum(PaymentMethod), nullable=False)
    payment_status = Column(String(20), default="pending")
    amount_paid = Column(DECIMAL, default=0.00)
    change_amount = Column(DECIMAL, default=0.00)
    sale_type = Column(Enum(SaleType), default=SaleType.LOCAL)
    status = Column(String(20), default="completed")
    delivery_address = Column(String(500), nullable=True)
    delivery_notes = Column(Text, nullable=True)
    external_order_id = Column(Integer, nullable=True)
    external_order_type = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)
    receipt_number = Column(String(50), nullable=True, unique=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    terminal = relationship("PDVTerminal", back_populates="sales")
    cash_register = relationship("PDVCashRegister", back_populates="sales")
    customer = relationship("User", foreign_keys=[customer_id], backref="pdv_purchases")
    seller = relationship("User", foreign_keys=[created_by], backref="pdv_sales_created")
    items = relationship("PDVSaleItem", back_populates="sale", cascade="all, delete-orphan")


class PDVSaleItem(Base):
    __tablename__ = "pdv_sale_items"

    id = Column(Integer, primary_key=True, index=True)
    sale_id = Column(Integer, ForeignKey("pdv_sales.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("pdv_products.id", ondelete="CASCADE"), nullable=False)
    product_name = Column(String(255), nullable=False)
    product_sku = Column(String(100), nullable=True)
    quantity = Column(DECIMAL, nullable=False)
    unit_price = Column(DECIMAL, nullable=False)
    discount_amount = Column(DECIMAL, default=0.00)
    discount_percent = Column(DECIMAL, default=0.00)
    subtotal = Column(DECIMAL, nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    sale = relationship("PDVSale", back_populates="items")
    product = relationship("PDVProduct", back_populates="sale_items")


class PDVCategory(Base):
    __tablename__ = "pdv_categories"

    id = Column(Integer, primary_key=True, index=True)
    terminal_id = Column(Integer, ForeignKey("pdv_terminals.id", ondelete="CASCADE"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    name = Column(String(100), nullable=False)
    description = Column(String(255), nullable=True)
    icon = Column(String(50), nullable=True)
    color = Column(String(20), nullable=True)
    is_global = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PDVPaymentMethod(Base):
    __tablename__ = "pdv_payment_methods"

    id = Column(Integer, primary_key=True, index=True)
    terminal_id = Column(Integer, ForeignKey("pdv_terminals.id", ondelete="CASCADE"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    name = Column(String(100), nullable=False)
    description = Column(String(255), nullable=True)
    icon = Column(String(50), nullable=True)
    is_global = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
