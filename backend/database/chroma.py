from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings

DATA_DIR = Path(__file__).parent.parent / "chroma_data"
COLLECTION_NAME = "documents"

_client: Optional["chromadb.ClientAPI"] = None
_collection = None


def _new_client() -> "chromadb.ClientAPI":
    # Disable telemetry to avoid opentelemetry version mismatches breaking startup.
    settings = Settings(anonymized_telemetry=False, allow_reset=True)
    return chromadb.PersistentClient(path=str(DATA_DIR), settings=settings)


def get_client() -> "chromadb.ClientAPI":
    global _client
    if _client is None:
        _client = _new_client()
    return _client


def _backup_data_dir() -> None:
    if not DATA_DIR.exists():
        return
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = DATA_DIR.with_name(f"{DATA_DIR.name}.bak-{ts}")
    DATA_DIR.rename(backup)


def get_collection():
    """Return the persistent Chroma collection, recovering from schema mismatches."""
    global _collection, _client
    if _collection is not None:
        return _collection

    client = get_client()
    try:
        _collection = client.get_or_create_collection(name=COLLECTION_NAME)
        return _collection
    except Exception:
        # Most common failure mode: on-disk persistence from an older Chroma version.
        # Try a soft reset first, then fall back to backing up the data dir.
        try:
            client.reset()
            _collection = client.get_or_create_collection(name=COLLECTION_NAME)
            return _collection
        except Exception:
            _backup_data_dir()
            _client = _new_client()
            _collection = _client.get_or_create_collection(name=COLLECTION_NAME)
            return _collection
