-- Script para adicionar campos de assinatura na tabela pdv_terminals
-- Usando tipos compatíveis com SQLAlchemy

-- Adicionar coluna subscription_status
ALTER TABLE pdv_terminals 
ADD COLUMN subscription_status VARCHAR(50) NOT NULL DEFAULT 'trial';

-- Adicionar coluna next_billing_date
ALTER TABLE pdv_terminals 
ADD COLUMN next_billing_date TIMESTAMP;

-- Adicionar coluna grace_period_ends_at
ALTER TABLE pdv_terminals 
ADD COLUMN grace_period_ends_at TIMESTAMP;
