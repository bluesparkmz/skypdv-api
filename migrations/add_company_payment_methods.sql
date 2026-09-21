-- Métodos de pagamento pertencem exclusivamente ao terminal/empresa.
-- PostgreSQL 12+ (executar uma vez, idealmente depois do deploy do código).

BEGIN;

-- 1. Cada venda passa a guardar o método de pagamento cadastrado pela empresa.
ALTER TABLE pdv_sales
    ADD COLUMN IF NOT EXISTS payment_method_id INTEGER;

ALTER TABLE pdv_sales
    DROP CONSTRAINT IF EXISTS fk_pdv_sales_payment_method;

ALTER TABLE pdv_sales
    ADD CONSTRAINT fk_pdv_sales_payment_method
    FOREIGN KEY (payment_method_id)
    REFERENCES pdv_payment_methods(id)
    ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_pdv_sales_payment_method_id
    ON pdv_sales(payment_method_id);

-- 2. Não existem mais métodos globais/partilhados.
-- Registos antigos globais não são removidos: são desativados para preservar histórico.
UPDATE pdv_payment_methods
SET is_active = FALSE
WHERE COALESCE(is_global, FALSE) = TRUE OR terminal_id IS NULL;

ALTER TABLE pdv_payment_methods
    ALTER COLUMN is_global SET DEFAULT FALSE;

-- 3. Impede nomes repetidos dentro da mesma empresa, ignorando maiúsculas/minúsculas.
CREATE UNIQUE INDEX IF NOT EXISTS uq_pdv_payment_methods_terminal_name_ci
    ON pdv_payment_methods (terminal_id, LOWER(name));

COMMIT;

-- Nota: não execute "SET NOT NULL" em terminal_id enquanto existirem métodos
-- globais antigos. Após os apagar ou associar manualmente a uma empresa:
-- ALTER TABLE pdv_payment_methods ALTER COLUMN terminal_id SET NOT NULL;
