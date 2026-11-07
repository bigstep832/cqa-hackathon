"""Shared SQLite engine and short serialized write transactions."""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.pool import ConnectionPoolEntry


@lru_cache(maxsize=8)
def _engine(database_url: str) -> Engine:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError("DATABASE_URL must select a persistent SQLite database")
    Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(engine, "connect")
    def configure(dbapi_connection: sqlite3.Connection, _record: ConnectionPoolEntry) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def get_engine() -> Engine:
    return _engine(os.environ.get("DATABASE_URL", "sqlite:///./data/private/cqa.sqlite3"))


@contextmanager
def write_transaction() -> Iterator[Connection]:
    with get_engine().connect() as connection:
        # Acquire the writer lock before reading the previous version.
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
