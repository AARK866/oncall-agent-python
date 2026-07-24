from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.engine import CursorResult, Row, URL, make_url
from sqlalchemy.pool import NullPool

from app.config import settings
from app.security_context import current_tenant_id, has_system_database_access
from app.storage.schema import metadata


class DatabaseRow(Mapping[str, Any]):
    """Row wrapper compatible with the existing sqlite3.Row access pattern."""

    def __init__(self, row: Row[Any]) -> None:
        self._row = row
        self._mapping = row._mapping

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._row[key]
        return self._mapping[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._mapping)

    def __len__(self) -> int:
        return len(self._mapping)


class DatabaseResult:
    def __init__(self, result: CursorResult[Any] | None) -> None:
        self._result = result

    @property
    def rowcount(self) -> int:
        return self._result.rowcount if self._result is not None else 0

    def fetchone(self) -> DatabaseRow | None:
        if self._result is None:
            return None
        row = self._result.fetchone()
        return DatabaseRow(row) if row is not None else None

    def fetchall(self) -> list[DatabaseRow]:
        if self._result is None:
            return []
        return [DatabaseRow(row) for row in self._result.fetchall()]


class DatabaseConnection:
    """Transactional SQLAlchemy connection with positional-parameter support."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._connection = None

    @property
    def dialect(self) -> str:
        return self._engine.dialect.name

    def __enter__(self) -> "DatabaseConnection":
        self._connection = self._engine.connect()
        if self.dialect == "postgresql":
            self._connection.execute(
                text(
                    "SELECT "
                    "set_config('app.tenant_id', :tenant_id, true), "
                    "set_config('app.system_access', :system_access, true)"
                ),
                {
                    "tenant_id": current_tenant_id(),
                    "system_access": (
                        "true" if has_system_database_access() else "false"
                    ),
                },
            )
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._connection is None:
            return
        try:
            if exc_type is None:
                self._connection.commit()
            else:
                self._connection.rollback()
        finally:
            self._connection.close()
            self._connection = None

    def execute(
        self,
        statement: str,
        parameters: Sequence[Any] | None = None,
    ) -> DatabaseResult:
        connection = self._require_connection()
        sql, bound = _bind_qmark_parameters(statement, parameters)
        return DatabaseResult(connection.execute(text(sql), bound))

    def executemany(
        self,
        statement: str,
        parameters: Sequence[Sequence[Any]],
    ) -> DatabaseResult:
        connection = self._require_connection()
        parameter_rows = list(parameters)
        if not parameter_rows:
            return DatabaseResult(None)
        sql, _ = _bind_qmark_parameters(statement, parameter_rows[0])
        bound_rows = [
            {f"p{index}": value for index, value in enumerate(row)}
            for row in parameter_rows
        ]
        return DatabaseResult(connection.execute(text(sql), bound_rows))

    def column_names(self, table_name: str) -> set[str]:
        return {
            str(column["name"])
            for column in inspect(self._require_connection()).get_columns(table_name)
        }

    def acquire_write_lock(
        self,
        table_name: str,
        key_column: str,
        key_value: Any,
    ) -> None:
        if table_name not in metadata.tables:
            raise ValueError(f"Unknown table for write lock: {table_name}")
        table = metadata.tables[table_name]
        if key_column not in table.c:
            raise ValueError(f"Unknown lock column: {table_name}.{key_column}")

        if self.dialect == "postgresql":
            self.execute(
                f"SELECT {key_column} FROM {table_name} "
                f"WHERE {key_column} = ? FOR UPDATE",
                (key_value,),
            )
            return

        if self.dialect == "sqlite":
            self._require_connection().exec_driver_sql("BEGIN IMMEDIATE")

    def _require_connection(self):
        if self._connection is None:
            raise RuntimeError("Database connection is not active.")
        return self._connection


class Database:
    def __init__(self, target: str | Path) -> None:
        self.url = normalize_database_url(target)
        self.engine = _engine_for_url(self.url)

    @property
    def dialect(self) -> str:
        return self.engine.dialect.name

    @property
    def safe_url(self) -> str:
        return self.url.render_as_string(hide_password=True)

    def connect(self) -> DatabaseConnection:
        return DatabaseConnection(self.engine)

    def create_schema(self) -> None:
        metadata.create_all(self.engine)

    def ping(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))


def configured_database_target(fallback_path: str | Path) -> str | Path:
    return settings.database_url or fallback_path


def database_from_settings() -> Database:
    return Database(configured_database_target(settings.incident_db_path))


def normalize_database_url(target: str | Path) -> URL:
    raw_target = str(target).strip()
    if "://" not in raw_target:
        path = Path(raw_target).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return URL.create("sqlite+pysqlite", database=str(path))

    url = make_url(raw_target)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    if url.drivername.startswith("sqlite") and url.database not in {None, ":memory:"}:
        database_path = Path(url.database).expanduser()
        if not database_path.is_absolute():
            database_path = database_path.resolve()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        url = url.set(database=str(database_path))
    return url


@lru_cache(maxsize=32)
def _engine_for_url(url: URL) -> Engine:
    engine_options: dict[str, Any] = {
        "pool_pre_ping": True,
    }
    if url.get_backend_name() == "sqlite":
        engine_options.update(
            {
                "connect_args": {"check_same_thread": False},
                "poolclass": NullPool,
            }
        )
    else:
        engine_options.update(
            {
                "pool_size": settings.database_pool_size,
                "max_overflow": settings.database_max_overflow,
                "pool_timeout": settings.database_pool_timeout_seconds,
                "pool_recycle": settings.database_pool_recycle_seconds,
            }
        )

    engine = create_engine(url, **engine_options)
    if url.get_backend_name() == "sqlite":
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


def _bind_qmark_parameters(
    statement: str,
    parameters: Sequence[Any] | None,
) -> tuple[str, dict[str, Any]]:
    values = tuple(parameters or ())
    placeholder_count = statement.count("?")
    if placeholder_count != len(values):
        if placeholder_count == 0 and not values:
            return statement, {}
        raise ValueError(
            "SQL placeholder count does not match parameter count: "
            f"{placeholder_count} != {len(values)}"
        )

    fragments = statement.split("?")
    sql_parts: list[str] = []
    for index, fragment in enumerate(fragments[:-1]):
        sql_parts.extend((fragment, f":p{index}"))
    sql_parts.append(fragments[-1])
    return "".join(sql_parts), {
        f"p{index}": value for index, value in enumerate(values)
    }
