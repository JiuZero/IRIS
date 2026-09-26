from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from iris.db.models import Base


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


def make_session(engine: Engine) -> Session:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()