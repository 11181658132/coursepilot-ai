import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from app.models import (
    ChatResponse,
    ConversationMessage,
    ConversationSummary,
    FeedbackResponse,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialise()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialise(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    course TEXT NOT NULL,
                    material_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    response_id TEXT,
                    citations_json TEXT NOT NULL DEFAULT '[]',
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    estimated_cost_usd REAL NOT NULL DEFAULT 0,
                    feedback TEXT CHECK(feedback IN ('helpful', 'unhelpful')),
                    feedback_note TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_conversation_created
                ON messages(conversation_id, created_at);
                """
            )

    def save_exchange(
        self,
        conversation_id: Optional[str],
        course: str,
        material_type: str,
        question: str,
        answer: ChatResponse,
    ) -> Tuple[str, str]:
        now = _utc_now()
        conversation_id = conversation_id or uuid.uuid4().hex
        user_message_id = uuid.uuid4().hex
        assistant_message_id = uuid.uuid4().hex
        title = " ".join(question.split())[:70] or "新对话"
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT id FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if existing:
                connection.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (now, conversation_id),
                )
            else:
                connection.execute(
                    """INSERT INTO conversations
                    (id, title, course, material_type, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (conversation_id, title, course, material_type, now, now),
                )
            connection.execute(
                """INSERT INTO messages
                (id, conversation_id, role, content, created_at)
                VALUES (?, ?, 'user', ?, ?)""",
                (user_message_id, conversation_id, question, now),
            )
            connection.execute(
                """INSERT INTO messages
                (id, conversation_id, role, content, response_id, citations_json,
                 evidence_json, latency_ms, estimated_cost_usd, created_at)
                VALUES (?, ?, 'assistant', ?, ?, ?, ?, ?, ?, ?)""",
                (
                    assistant_message_id,
                    conversation_id,
                    answer.answer,
                    answer.response_id,
                    json.dumps([item.model_dump() for item in answer.citations]),
                    json.dumps([item.model_dump() for item in answer.evidence]),
                    answer.latency_ms,
                    answer.estimated_cost_usd,
                    now,
                ),
            )
        return conversation_id, assistant_message_id

    def list_conversations(self) -> List[ConversationSummary]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT c.*, COUNT(m.id) AS message_count
                FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id
                GROUP BY c.id ORDER BY c.updated_at DESC"""
            ).fetchall()
        return [ConversationSummary(**dict(row)) for row in rows]

    def get_messages(self, conversation_id: str) -> List[ConversationMessage]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at, rowid",
                (conversation_id,),
            ).fetchall()
        return [self._message_from_row(row) for row in rows]

    def set_feedback(
        self, message_id: str, rating: str, note: Optional[str]
    ) -> FeedbackResponse:
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE messages SET feedback = ?, feedback_note = ?
                WHERE id = ? AND role = 'assistant'""",
                (rating, note, message_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("Assistant message not found")
        return FeedbackResponse(message_id=message_id, rating=rating)

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> ConversationMessage:
        payload = dict(row)
        payload["citations"] = json.loads(payload.pop("citations_json"))
        payload["evidence"] = json.loads(payload.pop("evidence_json"))
        return ConversationMessage(**payload)
