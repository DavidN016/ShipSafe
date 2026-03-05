import chromadb
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "chroma_data"

chroma_client = chromadb.PersistentClient(path=str(DATA_DIR))

collection = chroma_client.get_or_create_collection(name="documents")
