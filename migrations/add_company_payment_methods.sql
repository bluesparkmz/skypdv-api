-- Métodos de pagamento pertencem exclusivamente à empresa/terminal.
-- PostgreSQL 12+; executar uma única vez antes do deploy da versão.

BEGIN;

-- Vendas de produtos: referência ao método cadastrado e nome histórico.
ALTER TABLE pdv_sales
    ADD COLUMN IF NOT EXISTS payment_method_id INTEGER;

ALTER TABLE pdv_sales
    ALTER COLUMN payment_method TYPE VARCHAR(100) USING payment_method::text;

ALTER TABLE pdv_sales
    DROP CONSTRAINT IF EXISTS fk_pdv_sales_payment_method;

ALTER TABLE pdv_sales
    ADD CONSTRAINT fk_pdv_sales_payment_method
    FOREIGN KEY (payment_method_id)
    REFERENCES pdv_payment_methods(id)
    ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_pdv_sales_payment_method_id
    ON pdv_sales(payment_method_id);

-- Serviços seguem exactamente a mesma regra das vendas.
ALTER TABLE pdv_service_orders
    ADD COLUMN IF NOT EXISTS payment_method_id INTEGER;

ALTER TABLE pdv_service_orders
    ALTER COLUMN payment_method TYPE VARCHAR(100) USING payment_method::text;

ALTER TABLE pdv_service_orders
    DROP CONSTRAINT IF EXISTS fk_pdv_service_orders_payment_method;

ALTER TABLE pdv_service_orders
    ADD CONSTRAINT fk_pdv_service_orders_payment_method
    FOREIGN KEY (payment_method_id)
    REFERENCES pdv_payment_methods(id)
    ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_pdv_service_orders_payment_method_id
    ON pdv_service_orders(payment_method_id);

-- Métodos globais antigos não podem aparecer nas novas vendas/serviços.
UPDATE pdv_payment_methods
SET is_active = FALSE
WHERE COALESCE(is_global, FALSE) = TRUE OR terminal_id IS NULL;

ALTER TABLE pdv_payment_methods
    ALTER COLUMN is_global SET DEFAULT FALSE;

-- Um nome só pode existir uma vez por empresa, ignorando maiúsculas/minúsculas.
CREATE UNIQUE INDEX IF NOT EXISTS uq_pdv_payment_methods_terminal_name_ci
    ON pdv_payment_methods (terminal_id, LOWER(name));

COMMIT;

-- Após associar ou remover todos os registos globais antigos, pode reforçar:
-- ALTER TABLE pdv_payment_methods ALTER COLUMN terminal_id SET NOT NULL;
