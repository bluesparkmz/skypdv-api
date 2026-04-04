# SkyPDV Schemas - Pydantic validation schemas for PDV API
# Esquemas de validação para API do PDV

from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import List, Optional, Any
from datetime import datetime
from decimal import Decimal
from enum import Enum


# ===================================================================
# Enums - Tipos enumerados para validação
# ===================================================================

class SourceTypeEnum(str, Enum):
    """Tipo de origem do produto no PDV"""
    LOCAL = "local"
    FASTFOOD = "fastfood"
    SKYVENDA = "skyvenda"


class MovementTypeEnum(str, Enum):
    """Tipo de movimentação de estoque"""
    IN = "in"
    OUT = "out"
    ADJUSTMENT = "adjustment"
    SALE = "sale"
    RETURN = "return"
    TRANSFER = "transfer"


class StorageLocationEnum(str, Enum):
    """Locais de armazenamento no PDV"""
    BALCAO = "balcao"
    CONGELADO = "congelado"
    ARMAZEM = "armazem"


class PaymentMethodEnum(str, Enum):
    """Métodos de pagamento no PDV"""
    CASH = "cash"
    CARD = "card"
    SKYWALLET = "skywallet"
    MPESA = "mpesa"
    MIXED = "mixed"


class SaleTypeEnum(str, Enum):
    """Tipo de venda no PDV"""
    LOCAL = "local"
    DELIVERY = "delivery"
    ONLINE = "online"


# ===================================================================
# Terminal Schemas - Esquemas para terminal PDV
# ===================================================================

class PDVTerminalBase(BaseModel):
    """Base schema for PDV Terminal"""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    bio: Optional[str] = None
    logo: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    tax_rate: Decimal = Decimal("0.00")
    currency: str = "MZN"
    settings: Optional[dict] = None


class PDVTerminalCreate(PDVTerminalBase):
    """Schema for creating a PDV Terminal"""
    pass


class PDVTerminalUpdate(BaseModel):
    """Schema for updating a PDV Terminal"""
    name: Optional[str] = None
    description: Optional[str] = None
    bio: Optional[str] = None
    logo: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    tax_rate: Optional[Decimal] = None
    currency: Optional[str] = None
    settings: Optional[dict] = None
    active: Optional[bool] = None


class PDVTerminal(PDVTerminalBase):
    """Schema for PDV Terminal response"""
    id: int
    user_id: int
    active: bool
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class PDVTerminalRoleEnum(str, Enum):
    """Papéis/Permissões de usuários no terminal PDV"""
    ADMIN = "admin"
    CASHIER = "cashier"
    MANAGER = "manager"
    VIEWER = "viewer"


class PDVTerminalUserBase(BaseModel):
    """Base schema for PDV Terminal User"""
    role: PDVTerminalRoleEnum = PDVTerminalRoleEnum.CASHIER
    can_sell: bool = True
    can_open_cash_register: bool = True
    can_manage_products: bool = False
    can_manage_stock: bool = False
    can_view_reports: bool = True
    can_manage_users: bool = False


class PDVTerminalUserCreate(BaseModel):
    """Schema for adding a user to terminal by email"""
    email: str = Field(..., description="Email da conta BlueSpark Accounts")
    role: PDVTerminalRoleEnum = PDVTerminalRoleEnum.CASHIER
    can_sell: bool = True
    can_open_cash_register: bool = True
    can_manage_products: bool = False
    can_manage_stock: bool = False
    can_view_reports: bool = True
    can_manage_users: bool = False


class PDVTerminalUserUpdate(BaseModel):
    """Schema for updating terminal user permissions"""
    role: Optional[PDVTerminalRoleEnum] = None
    can_sell: Optional[bool] = None
    can_open_cash_register: Optional[bool] = None
    can_manage_products: Optional[bool] = None
    can_manage_stock: Optional[bool] = None
    can_view_reports: Optional[bool] = None
    can_manage_users: Optional[bool] = None
    is_active: Optional[bool] = None


class PDVTerminalUser(PDVTerminalUserBase):
    """Schema for PDV Terminal User response"""
    id: int
    terminal_id: int
    user_id: Optional[int] = None
    is_active: bool
    invited_by: Optional[int] = None
    invited_at: datetime
    joined_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    is_pending: bool = False
    invited_email: Optional[str] = None
    
    # User info
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


# ===================================================================
# Supplier Schemas - Esquemas para fornecedores
# ===================================================================

class PDVSupplierBase(BaseModel):
    """Base schema for PDV Supplier"""
    name: str = Field(..., min_length=1, max_length=255)
    source_type: SourceTypeEnum = SourceTypeEnum.LOCAL
    external_id: Optional[int] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None


class PDVSupplierCreate(PDVSupplierBase):
    """Schema for creating a PDV Supplier"""
    pass


class PDVSupplierUpdate(BaseModel):
    """Schema for updating a PDV Supplier"""
    name: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class PDVSupplier(PDVSupplierBase):
    """Schema for PDV Supplier response"""
    id: int
    terminal_id: int
    is_active: bool
    last_sync_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ConnectFastFoodRequest(BaseModel):
    """Schema for connecting a FastFood restaurant to PDV"""
    restaurant_id: int
    sync_products: bool = True  # Se deve importar produtos automaticamente


class ConnectSkyVendaRequest(BaseModel):
    """Schema for connecting SkyVenda products to PDV"""
    user_id: int  # ID do vendedor SkyVenda
    product_ids: Optional[List[int]] = None  # IDs específicos ou None para todos


# ===================================================================
# Product Schemas - Esquemas para produtos
# ===================================================================

class PDVProductBase(BaseModel):
    """Base schema for PDV Product"""
    name: str = Field(..., min_length=1, max_length=255)
    sku: Optional[str] = None
    barcode: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    cost_price: Optional[Decimal] = Decimal("0.00")
    price: Decimal = Field(..., gt=0)
    promotional_price: Optional[Decimal] = None
    image: Optional[str] = None
    emoji: Optional[str] = None  # Emoji para exibição no frontend
    is_fastfood: bool = False
    track_stock: bool = True
    allow_decimal_quantity: bool = False

    @field_validator("price", "cost_price", "promotional_price", mode="before")
    @classmethod
    def validate_money_fields(cls, v):
        if v is None:
            return v

        raw = str(v).strip()
        if not raw:
            return v

        if "e" in raw.lower():
            raise ValueError("Invalid price")

        dec = Decimal(raw)
        if not dec.is_finite():
            raise ValueError("Invalid price")

        if dec < 0:
            raise ValueError("Invalid price")

        if dec > Decimal("1000000000"):
            raise ValueError("Invalid price")

        return dec


class PDVProductCreate(PDVProductBase):
    """Schema for creating a PDV Product"""
    supplier_id: Optional[int] = None
    initial_stock: Optional[Decimal] = None  # Estoque inicial


class PDVProductUpdate(BaseModel):
    """Schema for updating a PDV Product"""
    name: Optional[str] = None
    sku: Optional[str] = None
    barcode: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    cost_price: Optional[Decimal] = None
    price: Optional[Decimal] = None
    promotional_price: Optional[Decimal] = None
    image: Optional[str] = None
    emoji: Optional[str] = None  # Emoji para exibição no frontend
    is_fastfood: Optional[bool] = None
    track_stock: Optional[bool] = None
    allow_decimal_quantity: Optional[bool] = None
    supplier_id: Optional[int] = None
    initial_stock: Optional[Decimal] = None
    is_active: Optional[bool] = None

    @field_validator("price", "cost_price", "promotional_price", mode="before")
    @classmethod
    def validate_money_fields(cls, v):
        if v is None:
            return v

        raw = str(v).strip()
        if not raw:
            return v

        if "e" in raw.lower():
            raise ValueError("Invalid price")

        dec = Decimal(raw)
        if not dec.is_finite():
            raise ValueError("Invalid price")

        if dec < 0:
            raise ValueError("Invalid price")

        if dec > Decimal("1000000000"):
            raise ValueError("Invalid price")

        return dec


class PDVProductImport(BaseModel):
    """Schema for importing products from external sources"""
    source_type: SourceTypeEnum
    supplier_id: int
    product_ids: Optional[List[int]] = None  # IDs específicos ou None para todos


class PDVInventoryInfo(BaseModel):
    """Inventory info embedded in product response"""
    quantity: Decimal
    min_quantity: Decimal
    max_quantity: Optional[Decimal] = None
    reserved_quantity: Decimal = Decimal("0.00")
    model_config = ConfigDict(from_attributes=True)


class PDVProduct(PDVProductBase):
    """Schema for PDV Product response"""
    id: int
    terminal_id: int
    supplier_id: Optional[int] = None
    source_type: SourceTypeEnum
    external_product_id: Optional[int] = None
    is_active: bool
    inventory: Optional[PDVInventoryInfo] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class PDVProductSearch(BaseModel):
    """Schema for product search"""
    query: Optional[str] = None
    category: Optional[str] = None
    source_type: Optional[SourceTypeEnum] = None
    supplier_id: Optional[int] = None
    is_active: Optional[bool] = True
    low_stock_only: bool = False
    is_fastfood: Optional[bool] = None
    skip: int = 0
    limit: int = 50


class PDVProductBatchFastFood(BaseModel):
    """Schema para marcar/desmarcar produtos como FastFood em lote"""
    product_ids: List[int]
    is_fastfood: bool


class PDVProductStats(BaseModel):
    """Estatísticas de produtos do terminal"""
    total_products: int
    active_products: int
    fastfood_products: int
    local_products: int
    categories_count: int


# ===================================================================
# Inventory Schemas - Esquemas para estoque
# ===================================================================

class PDVInventoryUpdate(BaseModel):
    """Schema for updating product inventory"""
    quantity: Optional[Decimal] = None
    min_quantity: Optional[Decimal] = None
    max_quantity: Optional[Decimal] = None
    storage_location: Optional[StorageLocationEnum] = None


class StockAdjustment(BaseModel):
    """Schema for stock adjustment"""
    product_id: int
    movement_type: MovementTypeEnum
    quantity: Decimal = Field(..., description="Quantity to add/remove (always positive)")
    notes: Optional[str] = None
    reference: Optional[str] = None
    storage_location: StorageLocationEnum = StorageLocationEnum.BALCAO


class StockTransfer(BaseModel):
    """Schema for stock transfer between locations"""
    product_id: int
    from_location: StorageLocationEnum
    to_location: StorageLocationEnum
    quantity: Decimal = Field(..., gt=0)
    notes: Optional[str] = None


class StockBulkAdjustment(BaseModel):
    """Schema for bulk stock adjustment"""
    adjustments: List[StockAdjustment]


class PDVStockMovement(BaseModel):
    """Schema for stock movement response"""
    id: int
    product_id: int
    terminal_id: int
    movement_type: MovementTypeEnum
    quantity: Decimal
    quantity_before: Optional[Decimal] = None
    quantity_after: Optional[Decimal] = None
    reference: Optional[str] = None
    reference_id: Optional[int] = None
    from_location: Optional[str] = None
    to_location: Optional[str] = None
    notes: Optional[str] = None
    created_by: Optional[int] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class PDVInventory(BaseModel):
    """Schema for inventory response"""
    id: int
    product_id: int
    terminal_id: int
    quantity: Decimal
    min_quantity: Decimal
    max_quantity: Optional[Decimal] = None
    reserved_quantity: Decimal
    storage_location: StorageLocationEnum = StorageLocationEnum.BALCAO
    last_restock_at: Optional[datetime] = None
    last_count_at: Optional[datetime] = None
    updated_at: datetime
    # Informações do produto incluídas
    product_name: Optional[str] = None
    product_sku: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class LowStockAlert(BaseModel):
    """Schema for low stock alert"""
    product_id: int
    product_name: str
    sku: Optional[str] = None
    current_quantity: Decimal
    min_quantity: Decimal
    shortage: Decimal  # Quanto falta para atingir o mínimo


# ===================================================================
# Cash Register Schemas - Esquemas para caixa
# ===================================================================

class PDVCashRegisterOpen(BaseModel):
    """Schema for opening a cash register"""
    opening_amount: Decimal = Decimal("0.00")
    notes: Optional[str] = None


class PDVCashRegisterClose(BaseModel):
    """Schema for closing a cash register"""
    closing_amount: Decimal
    notes: Optional[str] = None


class PDVCashRegister(BaseModel):
    """Schema for cash register response"""
    id: int
    terminal_id: int
    user_id: int
    opened_at: datetime
    closed_at: Optional[datetime] = None
    opening_amount: Decimal
    closing_amount: Optional[Decimal] = None
    expected_amount: Optional[Decimal] = None
    difference: Optional[Decimal] = None
    total_cash: Decimal
    total_card: Decimal
    total_skywallet: Decimal
    total_mpesa: Decimal
    total_sales: Decimal
    total_refunds: Decimal
    sales_count: int
    refunds_count: int
    status: str
    notes: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


# ===================================================================
# Sale Schemas - Esquemas para vendas
# ===================================================================

class PDVSaleItemCreate(BaseModel):
    """Schema for sale item creation"""
    product_id: int  # Agora obrigatório: apenas PDVProduct
    quantity: Decimal = Field(..., gt=0)
    unit_price: Optional[Decimal] = None  # Se None, usa preço do produto
    discount_amount: Decimal = Decimal("0.00")
    discount_percent: Decimal = Decimal("0.00")
    notes: Optional[str] = None


class PDVSaleCreate(BaseModel):
    """Schema for creating a sale"""
    items: List[PDVSaleItemCreate] = Field(..., min_length=1)
    customer_id: Optional[int] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    payment_method: PaymentMethodEnum
    amount_paid: Optional[Decimal] = None
    discount_amount: Decimal = Decimal("0.00")
    discount_percent: Decimal = Decimal("0.00")
    sale_type: SaleTypeEnum = SaleTypeEnum.LOCAL
    delivery_address: Optional[str] = None
    delivery_notes: Optional[str] = None
    notes: Optional[str] = None


class PDVSaleItem(BaseModel):
    """Schema for sale item response"""
    id: int
    sale_id: int
    product_id: int
    product_name: str
    product_sku: Optional[str] = None
    quantity: Decimal
    unit_price: Decimal
    discount_amount: Decimal
    discount_percent: Decimal
    subtotal: Decimal
    notes: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class PDVSale(BaseModel):
    """Schema for sale response in standalone SkyPDV"""
    id: int
    terminal_id: int
    cash_register_id: Optional[int] = None
    customer_id: Optional[int] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    subtotal: Decimal
    discount_amount: Decimal
    discount_percent: Decimal
    tax_amount: Decimal
    total: Decimal
    payment_method: PaymentMethodEnum
    payment_status: str
    amount_paid: Decimal
    change_amount: Decimal
    sale_type: SaleTypeEnum
    status: str
    delivery_address: Optional[str] = None
    delivery_notes: Optional[str] = None
    external_order_id: Optional[int] = None
    external_order_type: Optional[str] = None
    notes: Optional[str] = None
    receipt_number: Optional[str] = None
    items: List[PDVSaleItem] = []
    created_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class PDVSaleVoid(BaseModel):
    """Schema for voiding a sale"""
    reason: str = Field(..., min_length=1)
    refund_to_wallet: bool = False  # Se deve devolver para SkyWallet


# ===================================================================
# Report Schemas - Esquemas para relatórios
# ===================================================================

class DateRangeFilter(BaseModel):
    """Schema for date range filter"""
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


class SalesSummary(BaseModel):
    """Schema for sales summary report"""
    period_start: datetime
    period_end: datetime
    total_sales: int
    total_revenue: Decimal
    total_cost: Decimal
    gross_profit: Decimal
    average_sale_value: Decimal
    total_items_sold: int
    total_discounts: Decimal
    total_taxes: Decimal
    # Por método de pagamento
    cash_sales: Decimal
    card_sales: Decimal
    skywallet_sales: Decimal
    mpesa_sales: Decimal
    # Cancelamentos
    voided_sales: int
    voided_amount: Decimal


class InventoryReport(BaseModel):
    """Schema for inventory report"""
    total_products: int
    total_value: Decimal  # Valor total do estoque (custo)
    total_retail_value: Decimal  # Valor total do estoque (venda)
    low_stock_count: int
    out_of_stock_count: int
    products: List[PDVInventory] = []


class TopProduct(BaseModel):
    """Schema for top selling product"""
    product_id: int
    product_name: str
    category: Optional[str] = None
    quantity_sold: Decimal
    revenue: Decimal
    profit: Decimal


class SalesByPeriod(BaseModel):
    """Schema for sales by time period"""
    period: str  # e.g., "2024-01-15", "2024-W03", "2024-01"
    sales_count: int
    total_revenue: Decimal
    average_value: Decimal


class DashboardStats(BaseModel):
    """Schema for dashboard statistics"""
    # Hoje
    today_sales: int
    today_revenue: Decimal
    today_profit: Decimal
    # Esta semana
    week_sales: int
    week_revenue: Decimal
    # Este mês
    month_sales: int
    month_revenue: Decimal
    # Estoque
    low_stock_alerts: int
    out_of_stock: int
    # Caixa atual
    current_register_open: bool
    current_register_total: Optional[Decimal] = None
    # Top produtos
    top_products: List[TopProduct] = []
    # Breakdown por pagamento (Mês)
    payment_breakdown: Optional[dict] = {}
    # Breakdown por dia (Semana)
    weekly_breakdown: List[SalesByPeriod] = []




class DetailedMonthlyReport(BaseModel):
    """Schema for detailed monthly report"""
    year: int
    month: int
    month_name: str
    summary: SalesSummary
    daily_breakdown: List[SalesByPeriod]  # Vendas por dia do mês
    top_products: List[TopProduct]  # Top 10 produtos do mês
    top_categories: List[dict]  # Top categorias por receita
    payment_method_breakdown: dict  # Breakdown detalhado por método
    comparison_previous_month: Optional[dict] = None  # Comparação com mês anterior


class DetailedYearlyReport(BaseModel):
    """Schema for detailed yearly report"""
    year: int
    summary: SalesSummary
    monthly_breakdown: List[SalesByPeriod]  # Vendas por mês
    top_products: List[TopProduct]  # Top 20 produtos do ano
    top_categories: List[dict]  # Top categorias do ano
    payment_method_breakdown: dict
    seasonal_trends: Optional[dict] = None  # Tendências sazonais
    comparison_previous_year: Optional[dict] = None  # Comparação com ano anterior


# ===================================================================
# Category Schemas - Esquemas para categorias
# ===================================================================

class PDVCategoryBase(BaseModel):
    """Base schema for PDV Category"""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None


class PDVCategoryCreate(PDVCategoryBase):
    """Schema for creating a category"""
    pass


class PDVCategoryUpdate(BaseModel):
    """Schema for updating a category"""
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    is_active: Optional[bool] = None


class PDVCategory(PDVCategoryBase):
    """Schema for category response"""
    id: int
    terminal_id: Optional[int]
    is_global: bool = False
    created_by: Optional[int] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ===================================================================
# Payment Method Schemas - Esquemas para métodos de pagamento
# ===================================================================

class PDVPaymentMethodBase(BaseModel):
    """Base schema for PDV Payment Method"""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    icon: Optional[str] = None


class PDVPaymentMethodCreate(PDVPaymentMethodBase):
    """Schema for creating a payment method"""
    pass


class PDVPaymentMethodUpdate(BaseModel):
    """Schema for updating a payment method"""
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    is_active: Optional[bool] = None


class PDVPaymentMethod(PDVPaymentMethodBase):
    """Schema for payment method response"""
    id: int
    terminal_id: Optional[int]
    is_global: bool = False
    created_by: Optional[int] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ===================================================================
# Finance Schemas - Esquemas para financeiro
# ===================================================================

class PDVExpenseCategoryBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    code: str = Field(..., min_length=2, max_length=60)
    description: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None


class PDVExpenseCategoryCreate(PDVExpenseCategoryBase):
    pass


class PDVExpenseCategoryUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    is_active: Optional[bool] = None


class PDVExpenseCategory(PDVExpenseCategoryBase):
    id: int
    terminal_id: Optional[int] = None
    created_by: Optional[int] = None
    is_global: bool = False
    is_active: bool
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class PDVExpenseBase(BaseModel):
    category_id: Optional[int] = None
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    amount: Decimal = Field(..., gt=0)
    expense_date: datetime
    vendor_name: Optional[str] = None
    payment_method: Optional[str] = None
    reference: Optional[str] = None
    notes: Optional[str] = None


class PDVExpenseCreate(PDVExpenseBase):
    pass


class PDVExpenseUpdate(BaseModel):
    category_id: Optional[int] = None
    title: Optional[str] = None
    description: Optional[str] = None
    amount: Optional[Decimal] = Field(default=None, gt=0)
    expense_date: Optional[datetime] = None
    vendor_name: Optional[str] = None
    payment_method: Optional[str] = None
    reference: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class PDVExpense(PDVExpenseBase):
    id: int
    terminal_id: int
    created_by: Optional[int] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    category_name: Optional[str] = None
    category_code: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class ExpenseCategoryBreakdown(BaseModel):
    category_id: Optional[int] = None
    category_name: str
    category_code: Optional[str] = None
    total_amount: Decimal


class FinancialSummary(BaseModel):
    period_start: datetime
    period_end: datetime
    gross_revenue: Decimal
    gross_profit: Decimal
    total_expenses: Decimal
    net_profit: Decimal
    sales_count: int
    expenses_count: int
    expense_breakdown: List[ExpenseCategoryBreakdown] = []


# ==============================
# FastFood minimal support
# ==============================


class FastFoodRestaurantBase(BaseModel):
    name: str
    category: Optional[str] = None
    is_open: Optional[bool] = False
    active: Optional[bool] = True
    phone: Optional[str] = None
    address: Optional[str] = None


class FastFoodRestaurantCreate(FastFoodRestaurantBase):
    pass


class FastFoodRestaurant(FastFoodRestaurantBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RestaurantTableBase(BaseModel):
    table_number: str
    seats: int = 4
    shape: str = "square"
    width: int = 80
    height: int = 80
    position_x: int = 0
    position_y: int = 0
    status: str = "available"


class RestaurantTableCreate(RestaurantTableBase):
    pass


class RestaurantTableUpdate(BaseModel):
    table_number: Optional[str] = None
    seats: Optional[int] = None
    shape: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    position_x: Optional[int] = None
    position_y: Optional[int] = None
    status: Optional[str] = None


class RestaurantTablePosition(BaseModel):
    position_x: int
    position_y: int


class RestaurantTable(RestaurantTableBase):
    id: int
    restaurant_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

