import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional


class ChunkStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, signature TEXT NOT NULL,
                    course TEXT NOT NULL, material_type TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    path TEXT NOT NULL REFERENCES documents(path) ON DELETE CASCADE,
                    payload TEXT NOT NULL, embedding TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
            """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def manifest(self, signature: str) -> dict:
        with self.connect() as db:
            return {row["path"]: dict(row) for row in db.execute(
                "SELECT * FROM documents WHERE signature=?", (signature,)
            )}

    def replace(self, document, signature: str, chunks: List[dict], vectors: List[list]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("Embedding count does not match chunk count")
        with self.connect() as db:
            db.execute("DELETE FROM documents WHERE path=?", (document.relative_path,))
            db.execute("INSERT INTO documents VALUES (?, ?, ?, ?, ?)", (
                document.relative_path, document.sha256, signature,
                document.course, document.material_type,
            ))
            db.executemany("INSERT INTO chunks VALUES (?, ?, ?, ?)", [
                (chunk["chunk_id"], chunk["path"], json.dumps(chunk, ensure_ascii=False),
                 json.dumps(vector)) for chunk, vector in zip(chunks, vectors)
            ])

    def delete_document(self, path: str) -> bool:
        with self.connect() as db:
            return db.execute("DELETE FROM documents WHERE path=?", (path,)).rowcount > 0

    def rows(self, signature: str, course: str, material_type: str) -> List[dict]:
        sql = """SELECT c.payload, c.embedding, d.sha256 FROM chunks c
                 JOIN documents d ON d.path=c.path WHERE d.signature=? AND d.course=?"""
        params = [signature, course]
        if material_type != "all":
            sql += " AND d.material_type=?"
            params.append(material_type)
        with self.connect() as db:
            return [{**json.loads(row["payload"]), "embedding": json.loads(row["embedding"]),
                     "sha256": row["sha256"]} for row in db.execute(sql, params)]

    def source(self, chunk_id: str, signature: str) -> Optional[dict]:
        with self.connect() as db:
            row = db.execute("""SELECT c.payload, d.sha256 FROM chunks c
                JOIN documents d ON d.path=c.path WHERE c.chunk_id=? AND d.signature=?""",
                             (chunk_id, signature)).fetchone()
        return {**json.loads(row["payload"]), "sha256": row["sha256"]} if row else None
