"""SQLite + SQLAlchemy storage for users and connected repos."""

from __future__ import annotations

from pathlib import Path
from typing import Generator

from sqlalchemy import ForeignKey, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

# DB file next to backend (same level as chroma_data)
_backend_dir = Path(__file__).resolve().parent.parent
DATABASE_URL = f"sqlite:///{_backend_dir / 'shipsafe.db'}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    github_id: Mapped[str] = mapped_column(unique=True, index=True)
    login: Mapped[str] = mapped_column(nullable=False)
    connected_repos: Mapped[list["ConnectedRepo"]] = relationship(
        "ConnectedRepo", back_populates="user", cascade="all, delete-orphan"
    )


class ConnectedRepo(Base):
    __tablename__ = "connected_repos"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    repo_full_name: Mapped[str] = mapped_column(nullable=False, index=True)
    user: Mapped["User"] = relationship("User", back_populates="connected_repos")

    __table_args__ = (UniqueConstraint("user_id", "repo_full_name", name="uq_user_repo"),)


def init_db() -> None:
    """Create tables if they do not exist."""
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
