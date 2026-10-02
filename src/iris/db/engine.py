from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from iris.db.models import Base
from iris.log import get_logger

logger = get_logger(__name__)


def get_engine(url: str) -> Engine:
    if url.startswith("sqlite:///"):
        # ensure parent directory exists for file-based sqlite
        from pathlib import Path

        db_path = url.removeprefix("sqlite:///")
        parent = Path(db_path).parent
        if parent and str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, future=True)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    ensure_columns(engine)


#: Columns added after a database was first created. ``create_all`` only ever
#: creates missing *tables*, so an existing ``emulation_run`` would silently keep
#: the old shape and every insert naming a new column would fail at runtime --
#: which reads as "recording is broken", not as "this database predates it".
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "emulation_run": {
        "image_id": "INTEGER",
        "arch": "VARCHAR",
    },
}


def ensure_columns(engine: Engine) -> None:
    """Add columns that exist in the models but not yet in this database."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    for table, columns in _ADDED_COLUMNS.items():
        if table not in tables:
            continue
        present = {col["name"] for col in inspector.get_columns(table)}
        for name, decl in columns.items():
            if name in present:
                continue
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {decl}"))
            logger.info(f"added column {table}.{name} to an existing database")


def make_session(engine: Engine) -> Session:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()