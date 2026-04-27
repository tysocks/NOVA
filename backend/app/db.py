from contextlib import contextmanager
from typing import Iterator

import psycopg2
from psycopg2.extensions import connection as PGConnection

from .config import settings


@contextmanager
def get_conn(
    db_name: str | None = None,
    db_host: str | None = None,
    db_port: int | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
    db_sslmode: str | None = None,
) -> Iterator[PGConnection]:
    dsn = (
        f"host={db_host or settings.db_host} "
        f"port={db_port or settings.db_port} "
        f"dbname={db_name or settings.db_name} "
        f"user={db_user or settings.db_user} "
        f"password={db_password if db_password is not None else settings.db_password} "
        f"sslmode={db_sslmode or settings.db_sslmode}"
    )
    conn = psycopg2.connect(dsn)
    try:
        yield conn
    finally:
        conn.close()
