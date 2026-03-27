"""SQLite + SQLAlchemy storage for users, connected repos, and scan findings."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Generator, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    text,
)
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
    github_hook_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    user: Mapped["User"] = relationship("User", back_populates="connected_repos")

    __table_args__ = (UniqueConstraint("user_id", "repo_full_name", name="uq_user_repo"),)


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # webhook_push, webhook_pr, prepush
    repository: Mapped[Optional[str]] = mapped_column(String(256), nullable=True, index=True)  # owner/repo
    commit_sha: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[Any] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    results: Mapped[list["ScanResult"]] = relationship(
        "ScanResult", back_populates="scan_run", cascade="all, delete-orphan"
    )


class ScanResult(Base):
    __tablename__ = "scan_results"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scan_run_id: Mapped[int] = mapped_column(
        ForeignKey("scan_runs.id"), nullable=False, index=True
    )
    file_path: Mapped[str] = mapped_column(String(512), nullable=False, index=True)

    vulnerabilities_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    auditor_confirmed_vulnerable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    audit_feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    remediation_patch: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    scan_run: Mapped["ScanRun"] = relationship("ScanRun", back_populates="results")


def init_db() -> None:
    """Create tables if they do not exist."""
    Base.metadata.create_all(bind=engine)
    _migrate_sqlite_connected_repos_hook_id()


def _migrate_sqlite_connected_repos_hook_id() -> None:
    """Add github_hook_id column to existing SQLite DBs."""
    if not str(engine.url).startswith("sqlite"):
        return
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(connected_repos)")).fetchall()
        col_names = {row[1] for row in rows}
        if "github_hook_id" not in col_names:
            conn.execute(
                text("ALTER TABLE connected_repos ADD COLUMN github_hook_id INTEGER")
            )


def is_repository_connected(db, repo_full_name: str) -> bool:
    """True if at least one user connected this repo (for webhook allowlist)."""
    n = (
        db.query(ConnectedRepo)
        .filter(ConnectedRepo.repo_full_name == repo_full_name)
        .count()
    )
    return n > 0


def record_scan_results(
    db,
    *,
    source: str,
    repository: str | None,
    commit_sha: str | None,
    results: list[dict[str, Any]],
) -> int:
    """
    Persist a scan run and per-file results.

    `results` items are expected to look like backend.main workflow output:
      {file_path, vulnerabilities, is_verified, auditor_confirmed_vulnerable, audit_feedback, remediation_patch}
    """
    import json as _json

    run = ScanRun(source=source, repository=repository, commit_sha=commit_sha)
    db.add(run)
    db.flush()  # assigns run.id

    for r in results:
        vulns = r.get("vulnerabilities") or []
        auditor_confirmed = bool(
            r.get("auditor_confirmed_vulnerable", bool(r.get("is_verified")) and bool(vulns))
        )
        db.add(
            ScanResult(
                scan_run_id=run.id,
                file_path=str(r.get("file_path") or ""),
                vulnerabilities_json=_json.dumps(vulns),
                auditor_confirmed_vulnerable=auditor_confirmed,
                audit_feedback=(r.get("audit_feedback") or None),
                remediation_patch=(r.get("remediation_patch") or None),
            )
        )

    db.commit()
    return int(run.id)


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
