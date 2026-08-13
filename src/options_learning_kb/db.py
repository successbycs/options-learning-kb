from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from pgvector.psycopg import register_vector
from psycopg import Connection
from psycopg_pool import ConnectionPool


class Database:
    def __init__(self, database_url: str):
        self.pool = ConnectionPool(
            conninfo=database_url,
            min_size=1,
            max_size=4,
            open=True,
            kwargs={"autocommit": False},
        )

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        with self.pool.connection() as connection:
            register_vector(connection)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL search_path TO options_learning_kb, public")
            yield connection

    def close(self) -> None:
        self.pool.close()

    def ping(self) -> None:
        with self.connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1")
