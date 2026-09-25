from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import config

engine: Engine = create_engine(url=config.db_url, connect_args={"check_same_thread": False})
SessionLocal: sessionmaker[Session] = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db_session() -> Generator[Session]:
    """
    Unit of Work Dependency:
    Controls the database transaction for the entire request lifecycle.
    """
    db_session: Session = SessionLocal()
    try:
        yield db_session
        db_session.commit()
    except Exception:
        db_session.rollback()
        raise
    finally:
        db_session.close()


db_context = contextmanager(get_db_session)
