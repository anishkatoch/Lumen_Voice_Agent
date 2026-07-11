from logging.config import fileConfig
import psycopg2
from sqlalchemy import create_engine, pool
from alembic import context

from app.config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
from app.models import Base

config = context.config

# Pass DB params directly to psycopg2 — no URL building, no encoding issues.
# Special characters like $ in passwords work without any escaping.

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _make_engine():
    def creator():
        return psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            dbname=DB_NAME,
        )
    return create_engine("postgresql+psycopg2://", creator=creator, poolclass=pool.NullPool)


def run_migrations_offline() -> None:
    # Offline mode generates SQL without connecting — use DATABASE_URL here
    from app.config import DATABASE_URL
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = _make_engine()
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
