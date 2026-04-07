import argparse
import os
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import create_engine, inspect


DEFAULT_DB_URL = "postgresql://postgres:KuQPorjomhcopfkDaflSseLuNoSnyIPa@junction.proxy.rlwy.net:47053/railway"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def normalize_db_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        raise RuntimeError("Database URL is required.")

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
    parser = argparse.ArgumentParser(
        description="Create missing SQLAlchemy tables for the SkyPDV backend."
    )
    parser.add_argument(
        "--db-url",
        default=DEFAULT_DB_URL,
        help="PostgreSQL connection string.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    db_url = normalize_db_url(args.db_url)
    os.environ["DATABASE_URL"] = db_url

    from database import Base  # noqa: E402
    import models  # noqa: F401,E402

    engine = create_engine(db_url)
    inspector = inspect(engine)
    before_tables = set(inspector.get_table_names())

    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    after_tables = set(inspector.get_table_names())
    created_tables = sorted(after_tables - before_tables)

    print(f"Database synchronized successfully: {db_url}")
    if created_tables:
        print("Created tables:")
        for table_name in created_tables:
            print(f"- {table_name}")
    else:
        print("No new tables were created. Existing tables were kept as-is.")


if __name__ == "__main__":
    main()
