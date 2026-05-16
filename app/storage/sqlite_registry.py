import json
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from app.schemas.chunk import DocumentChunk
from app.schemas.document import ParsedDocument


class SQLiteRegistry:
    def __init__(self, db_path: Path):
        self.db_path = db_path.resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.db_path.as_posix(), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON;")
        self.initialize_schema()

    def initialize_schema(self) -> None:
        schema_path = Path(__file__).resolve().parent / "sql_schema.sql"
        schema_sql = schema_path.read_text(encoding="utf-8")
        self._connection.executescript(schema_sql)
        self._migrate_url_columns()
        self._connection.commit()

    def _migrate_url_columns(self) -> None:
        existing = {row[1] for row in self._connection.execute("PRAGMA table_info(documents)").fetchall()}
        if "source_type" not in existing:
            self._connection.execute("ALTER TABLE documents ADD COLUMN source_type TEXT NOT NULL DEFAULT 'local'")
        if "source_url" not in existing:
            self._connection.execute("ALTER TABLE documents ADD COLUMN source_url TEXT")
        if "ingested_at" not in existing:
            self._connection.execute("ALTER TABLE documents ADD COLUMN ingested_at DATETIME")
        self._connection.execute("UPDATE documents SET source_type = 'local' WHERE source_type IS NULL")
        self._connection.execute("UPDATE documents SET ingested_at = CURRENT_TIMESTAMP WHERE ingested_at IS NULL")

    def close(self) -> None:
        self._connection.close()

    def upsert_document(self, document_id: str, document: ParsedDocument) -> None:
        metadata_json = json.dumps(document.metadata, sort_keys=True)
        self._connection.execute(
            """
            INSERT INTO documents (
                document_id,
                source_path,
                file_name,
                file_type,
                checksum_sha256,
                parser_name,
                content_length,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                source_path = excluded.source_path,
                file_name = excluded.file_name,
                file_type = excluded.file_type,
                checksum_sha256 = excluded.checksum_sha256,
                parser_name = excluded.parser_name,
                content_length = excluded.content_length,
                metadata_json = excluded.metadata_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                document_id,
                document.source_path.as_posix(),
                document.filename,
                document.extension,
                document.checksum_sha256,
                document.parser_name,
                document.char_count,
                metadata_json,
            ),
        )
        self._connection.commit()

    def upsert_chunk(self, chunk: DocumentChunk) -> None:
        metadata_json = json.dumps(chunk.metadata, sort_keys=True)
        self._connection.execute(
            """
            INSERT INTO chunks (
                chunk_id,
                document_id,
                chunk_index,
                text,
                token_count,
                char_start,
                char_end,
                source_path,
                file_name,
                document_checksum_sha256,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chunk_id) DO UPDATE SET
                document_id = excluded.document_id,
                chunk_index = excluded.chunk_index,
                text = excluded.text,
                token_count = excluded.token_count,
                char_start = excluded.char_start,
                char_end = excluded.char_end,
                source_path = excluded.source_path,
                file_name = excluded.file_name,
                document_checksum_sha256 = excluded.document_checksum_sha256,
                metadata_json = excluded.metadata_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                chunk.chunk_id,
                chunk.document_id,
                chunk.chunk_index,
                chunk.text,
                chunk.token_count,
                chunk.char_start,
                chunk.char_end,
                chunk.source_path.as_posix(),
                chunk.file_name,
                chunk.document_checksum_sha256,
                metadata_json,
            ),
        )
        self._connection.commit()

    def get_document(self, document_id: str) -> Optional[Dict[str, object]]:
        row = self._connection.execute(
            "SELECT * FROM documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        return self._row_to_dict(row)

    def get_chunk(self, chunk_id: str) -> Optional[Dict[str, object]]:
        row = self._connection.execute(
            "SELECT * FROM chunks WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchone()
        return self._row_to_dict(row)

    def get_chunks_for_document(self, document_id: str) -> List[Dict[str, object]]:
        rows = self._connection.execute(
            "SELECT * FROM chunks WHERE document_id = ? ORDER BY chunk_index ASC",
            (document_id,),
        ).fetchall()
        return [self._row_to_dict(row) for row in rows if row is not None]

    def create_session(self, session_id: str, title: str = "") -> None:
        self._connection.execute(
            "INSERT OR IGNORE INTO chat_sessions (session_id, title) VALUES (?, ?)",
            (session_id, title),
        )
        self._connection.commit()

    def append_turn(self, session_id: str, turn_id: str, role: str, content: str, turn_index: int) -> None:
        self._connection.execute(
            """
            INSERT INTO chat_turns (turn_id, session_id, role, content, turn_index)
            VALUES (?, ?, ?, ?, ?)
            """,
            (turn_id, session_id, role, content, turn_index),
        )
        self._connection.execute(
            "UPDATE chat_sessions SET updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
            (session_id,),
        )
        self._connection.commit()

    def get_session_turns(self, session_id: str) -> List[Dict[str, object]]:
        rows = self._connection.execute(
            "SELECT turn_id, session_id, role, content, turn_index, created_at FROM chat_turns WHERE session_id = ? ORDER BY turn_index ASC",
            (session_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_sessions(self, limit: int = 20) -> List[Dict[str, object]]:
        rows = self._connection.execute(
            "SELECT session_id, title, created_at, updated_at FROM chat_sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def update_session_title(self, session_id: str, title: str) -> None:
        self._connection.execute(
            "UPDATE chat_sessions SET title = ? WHERE session_id = ?",
            (title, session_id),
        )
        self._connection.commit()

    def delete_session(self, session_id: str) -> None:
        self._connection.execute(
            "DELETE FROM chat_turns WHERE session_id = ?", (session_id,)
        )
        self._connection.execute(
            "DELETE FROM chat_sessions WHERE session_id = ?", (session_id,)
        )
        self._connection.commit()

    def insert_fact(self, fact_id: str, content: str, category: str, source: str = "user", confidence_score: float = 1.0) -> None:
        self._connection.execute(
            """
            INSERT OR REPLACE INTO learned_facts
            (fact_id, content, category, source, confidence_score)
            VALUES (?, ?, ?, ?, ?)
            """,
            (fact_id, content, category, source, confidence_score),
        )
        self._connection.commit()

    def list_facts(self, category: Optional[str] = None) -> List[Dict[str, object]]:
        if category:
            rows = self._connection.execute(
                "SELECT fact_id, content, category, source, confidence_score, created_at, usage_count FROM learned_facts WHERE category = ? ORDER BY created_at DESC",
                (category,),
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT fact_id, content, category, source, confidence_score, created_at, usage_count FROM learned_facts ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_fact(self, fact_id: str) -> None:
        self._connection.execute(
            "DELETE FROM learned_facts WHERE fact_id = ?",
            (fact_id,),
        )
        self._connection.commit()

    def get_fact(self, fact_id: str) -> Optional[Dict[str, object]]:
        row = self._connection.execute(
            "SELECT fact_id, content, category, source, confidence_score, created_at, usage_count FROM learned_facts WHERE fact_id = ?",
            (fact_id,),
        ).fetchone()
        return dict(row) if row else None

    def increment_fact_usage(self, fact_id: str) -> None:
        self._connection.execute(
            "UPDATE learned_facts SET usage_count = usage_count + 1, last_used_at = CURRENT_TIMESTAMP WHERE fact_id = ?",
            (fact_id,),
        )
        self._connection.commit()

    def get_or_create_whatsapp_session(self, phone_number: str) -> str:
        row = self._connection.execute(
            "SELECT session_id FROM whatsapp_sessions WHERE phone_number = ?",
            (phone_number,),
        ).fetchone()
        if row:
            return row["session_id"]
        session_id = str(uuid.uuid4())
        self._connection.execute(
            "INSERT INTO whatsapp_sessions (phone_number, session_id) VALUES (?, ?)",
            (phone_number, session_id),
        )
        self.create_session(session_id=session_id, title=f"WhatsApp {phone_number}")
        self._connection.commit()
        return session_id

    def get_or_create_named_session(self, name: str) -> str:
        row = self._connection.execute(
            "SELECT session_id FROM named_sessions WHERE name = ?",
            (name,),
        ).fetchone()
        if row:
            return row["session_id"]
        session_id = str(uuid.uuid4())
        self._connection.execute(
            "INSERT INTO named_sessions (name, session_id) VALUES (?, ?)",
            (name, session_id),
        )
        self.create_session(session_id=session_id, title=name)
        self._connection.commit()
        return session_id

    def update_whatsapp_last_active(self, phone_number: str) -> None:
        self._connection.execute(
            "UPDATE whatsapp_sessions SET last_active = CURRENT_TIMESTAMP WHERE phone_number = ?",
            (phone_number,),
        )
        self._connection.commit()

    def record_whatsapp_message_sent(self, usage_date: Optional[date] = None) -> int:
        day = (usage_date or date.today()).isoformat()
        self._connection.execute(
            """
            INSERT INTO whatsapp_usage_daily (usage_date, sent_count, updated_at)
            VALUES (?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(usage_date) DO UPDATE SET
                sent_count = sent_count + 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (day,),
        )
        row = self._connection.execute(
            "SELECT sent_count FROM whatsapp_usage_daily WHERE usage_date = ?",
            (day,),
        ).fetchone()
        self._connection.commit()
        return int(row["sent_count"])

    def get_whatsapp_usage_today(self, daily_limit: int = 50) -> Dict[str, object]:
        day = date.today().isoformat()
        row = self._connection.execute(
            "SELECT sent_count FROM whatsapp_usage_daily WHERE usage_date = ?",
            (day,),
        ).fetchone()
        sent_count = int(row["sent_count"]) if row else 0
        return {
            "date": day,
            "sent_count": sent_count,
            "daily_limit": daily_limit,
            "remaining": max(daily_limit - sent_count, 0),
        }

    def get_chat_usage_today(self) -> Dict[str, object]:
        day = date.today().isoformat()
        rows = self._connection.execute(
            """
            SELECT
                CASE
                    WHEN ws.session_id IS NOT NULL THEN 'whatsapp'
                    WHEN ns.name LIKE 'cli:%' THEN 'cli'
                    ELSE 'other'
                END AS source,
                COUNT(*) AS count
            FROM chat_turns ct
            LEFT JOIN whatsapp_sessions ws ON ws.session_id = ct.session_id
            LEFT JOIN named_sessions ns ON ns.session_id = ct.session_id
            WHERE ct.role = 'user'
              AND DATE(ct.created_at) = ?
            GROUP BY source
            """,
            (day,),
        ).fetchall()
        counts = {"cli": 0, "whatsapp": 0, "other": 0}
        for row in rows:
            counts[row["source"]] = int(row["count"])
        counts["date"] = day
        counts["total"] = counts["cli"] + counts["whatsapp"] + counts["other"]
        return counts

    def has_whatsapp_usage_alert_sent(self, threshold: int, usage_date: Optional[date] = None) -> bool:
        day = (usage_date or date.today()).isoformat()
        row = self._connection.execute(
            """
            SELECT 1 FROM whatsapp_usage_alerts
            WHERE usage_date = ? AND threshold = ?
            """,
            (day, threshold),
        ).fetchone()
        return row is not None

    def mark_whatsapp_usage_alert_sent(self, threshold: int, usage_date: Optional[date] = None) -> None:
        day = (usage_date or date.today()).isoformat()
        self._connection.execute(
            """
            INSERT OR IGNORE INTO whatsapp_usage_alerts (usage_date, threshold)
            VALUES (?, ?)
            """,
            (day, threshold),
        )
        self._connection.commit()

    def create_todo(
        self,
        title: str,
        list_name: Optional[str] = None,
        due_at: Optional[datetime] = None,
    ) -> Dict[str, object]:
        todo_id = str(uuid.uuid4())
        self._connection.execute(
            """
            INSERT INTO todos (id, title, list_name, due_at)
            VALUES (?, ?, ?, ?)
            """,
            (todo_id, title, list_name, self._format_datetime(due_at)),
        )
        self._connection.commit()
        todo = self.get_todo(todo_id)
        if todo is None:
            raise RuntimeError("Created todo could not be read back from SQLite")
        return todo

    def get_todo(self, todo_id: str) -> Optional[Dict[str, object]]:
        row = self._connection.execute(
            "SELECT * FROM todos WHERE id = ?",
            (todo_id,),
        ).fetchone()
        return self._row_to_dict(row)

    def get_pending_todos(self) -> List[Dict[str, object]]:
        now = self._format_datetime(datetime.now())
        rows = self._connection.execute(
            """
            SELECT * FROM todos
            WHERE due_at IS NOT NULL
              AND due_at > ?
              AND completed_at IS NULL
              AND notified_at IS NULL
            ORDER BY due_at ASC
            """,
            (now,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_todos_due_soon(self, minutes_ahead: int = 60) -> List[Dict[str, object]]:
        now = datetime.now()
        cutoff = now + timedelta(minutes=minutes_ahead)
        rows = self._connection.execute(
            """
            SELECT * FROM todos
            WHERE due_at IS NOT NULL
              AND due_at <= ?
              AND completed_at IS NULL
              AND notified_at IS NULL
            ORDER BY due_at ASC
            """,
            (self._format_datetime(cutoff),),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_todo_notified(self, todo_id: str) -> None:
        self._connection.execute(
            "UPDATE todos SET notified_at = ? WHERE id = ?",
            (self._format_datetime(datetime.now()), todo_id),
        )
        self._connection.commit()

    def mark_todo_completed(self, todo_id: str) -> None:
        self._connection.execute(
            "UPDATE todos SET completed_at = ? WHERE id = ?",
            (self._format_datetime(datetime.now()), todo_id),
        )
        self._connection.commit()

    def set_nudge_context(self, phone_number: str, habit_id: str) -> None:
        self._connection.execute(
            """
            INSERT INTO nudge_context (phone_number, habit_id, sent_at)
            VALUES (?, ?, ?)
            ON CONFLICT(phone_number) DO UPDATE SET
                habit_id = excluded.habit_id,
                sent_at = excluded.sent_at
            """,
            (phone_number, habit_id, self._format_datetime(datetime.now())),
        )
        self._connection.commit()

    def get_nudge_context(self, phone_number: str) -> Optional[str]:
        expires_after = self._format_datetime(datetime.now() - timedelta(hours=24))
        row = self._connection.execute(
            """
            SELECT habit_id FROM nudge_context
            WHERE phone_number = ? AND sent_at >= ?
            """,
            (phone_number, expires_after),
        ).fetchone()
        return row["habit_id"] if row else None

    def clear_nudge_context(self, phone_number: str) -> None:
        self._connection.execute(
            "DELETE FROM nudge_context WHERE phone_number = ?",
            (phone_number,),
        )
        self._connection.commit()

    def set_document_source(self, document_id: str, source_type: str, source_url: Optional[str] = None) -> None:
        self._connection.execute(
            "UPDATE documents SET source_type = ?, source_url = ?, ingested_at = CURRENT_TIMESTAMP WHERE document_id = ?",
            (source_type, source_url, document_id),
        )
        self._connection.commit()

    def is_url_ingested(self, source_url: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM documents WHERE source_url = ? AND source_type = 'url' LIMIT 1",
            (source_url,),
        ).fetchone()
        return row is not None

    def list_url_sources(self) -> List[Dict[str, object]]:
        rows = self._connection.execute(
            """
            SELECT document_id, file_name, source_url, ingested_at
            FROM documents
            WHERE source_type = 'url'
            ORDER BY ingested_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def list_all_sources(self) -> List[Dict[str, object]]:
        rows = self._connection.execute(
            """
            SELECT document_id, file_name, source_path, source_type, source_url, ingested_at
            FROM documents
            ORDER BY ingested_at DESC, created_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, object]]:
        if row is None:
            return None
        data = dict(row)
        for key in ("metadata_json",):
            if key in data and isinstance(data[key], str):
                data[key] = json.loads(data[key])
        return data

    @staticmethod
    def _format_datetime(value: Optional[datetime]) -> Optional[str]:
        if value is None:
            return None
        return value.replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
