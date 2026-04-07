import argparse
import os
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import create_engine, text


DEFAULT_DB_URL = "postgresql://postgres:KuQPorjomhcopfkDaflSseLuNoSnyIPa@junction.proxy.rlwy.net:47053/railway"


def normalize_db_url(url: str) -> str:
    value = (url or "").strip()
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://"):]

    parsed = urlparse(value)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    host = parsed.hostname or ""
    if "sslmode" not in query and "rlwy.net" in host:
        query["sslmode"] = "require"

    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        urlencode(query),
        parsed.fragment,
    ))


def parse_args():
    parser = argparse.ArgumentParser(description="Add shared catalog columns to pdv_products.")
    parser.add_argument("--db-url", default=os.getenv("DATABASE_URL") or DEFAULT_DB_URL)
    return parser.parse_args()


def main():
    args = parse_args()
    engine = create_engine(normalize_db_url(args.db_url))

    statements = [
        """
        ALTER TABLE pdv_products
        ADD COLUMN IF NOT EXISTS shared_source_product_id INTEGER NULL
        """,
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM information_schema.table_constraints
                WHERE constraint_name = 'fk_pdv_products_shared_source_product_id'
                  AND table_name = 'pdv_products'
            ) THEN
                ALTER TABLE pdv_products
                ADD CONSTRAINT fk_pdv_products_shared_source_product_id
                FOREIGN KEY (shared_source_product_id) REFERENCES pdv_products(id) ON DELETE SET NULL;
            END IF;
        END $$;
        """,
    ]

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))

    print("pdv_products updated for shared catalog support.", flush=True)


if __name__ == "__main__":
    main()
