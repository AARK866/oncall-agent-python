from logging.config import fileConfig

from alembic import context

from app.storage.database import Database, database_from_settings
from app.storage.schema import metadata


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

arguments = context.get_x_argument(as_dictionary=True)
database = database_from_settings()
if arguments.get("database_url"):
    database = Database(arguments["database_url"])
database_url = database.url.render_as_string(hide_password=False).replace("%", "%%")
config.set_main_option("sqlalchemy.url", database_url)
target_metadata = metadata


def _compare_server_default(
    _context,
    inspected_column,
    metadata_column,
    _inspected_default,
    _metadata_default,
    _rendered_metadata_default,
):
    if (
        inspected_column.name == "tenant_id"
        and metadata_column.name == "tenant_id"
    ):
        return False
    return None


def _configure_context(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=_compare_server_default,
        render_as_batch=database.dialect == "sqlite",
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure_context(
        url=database_url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with database.engine.connect() as connection:
        _configure_context(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
