"""Utilitário rápido para criar as novas tabelas FastFood via SQLAlchemy.

Execute com:  `python create_fastfood_tables.py`
Pré-requisitos: variáveis de ambiente do DB já configuradas em `database.py`.
"""

from database import engine
import models  # noqa: F401  # garante registro das classes no Base
from models import Base


def main() -> None:
    # Cria apenas as tabelas ausentes sem apagar nada existente.
    Base.metadata.create_all(bind=engine)
    print("Tabelas criadas/atualizadas com sucesso.")


if __name__ == "__main__":
    main()
