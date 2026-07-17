from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from app.config import load_settings
from app.repositories.mysql import MySqlDatabase, apply_migrations


def main() -> None:
    load_dotenv()
    database = MySqlDatabase(load_settings())
    applied = apply_migrations(database, Path(__file__).resolve().parents[1] / "migrations")
    if applied:
        print(f"Applied migrations: {', '.join(applied)}")
    else:
        print("Database schema is up to date.")


if __name__ == "__main__":
    main()
