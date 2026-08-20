from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import load_server_settings


class Base(DeclarativeBase):
    pass


def create_database(database_url: str | None = None):
    url = database_url or load_server_settings().database_url
    kwargs = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine_kwargs = {"poolclass": StaticPool} if url == "sqlite://" else {}
    engine = create_engine(url, connect_args=kwargs, future=True, **engine_kwargs)
    return engine, sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
