import binascii
import hashlib
import json
import os
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from app.schemas.chunk import DocumentChunk
from app.schemas.document import ParsedDocument

def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return binascii.hexlify(salt).decode() + ":" + binascii.hexlify(key).decode()


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, key_hex = stored_hash.split(":")
        salt = binascii.unhexlify(salt_hex)
        key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
        return binascii.hexlify(key).decode() == key_hex
    except Exception:
        return False


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
        self._migrate_columns()
        self._connection.commit()

    def _migrate_columns(self) -> None:
        """Add any missing columns to existing tables (idempotent)."""
        def _cols(table: str) -> set:
            return {row[1] for row in self._connection.execute(f"PRAGMA table_info({table})").fetchall()}

        # documents
        doc_cols = _cols("documents")
        for col, definition in [
            ("source_type", "TEXT NOT NULL DEFAULT 'local'"),
            ("source_url", "TEXT"),
            ("ingested_at", "DATETIME"),
            ("user_id", "TEXT NOT NULL DEFAULT 'default'"),
        ]:
            if col not in doc_cols:
                self._connection.execute(f"ALTER TABLE documents ADD COLUMN {col} {definition}")
        self._connection.execute("UPDATE documents SET source_type = 'local' WHERE source_type IS NULL")
        self._connection.execute("UPDATE documents SET ingested_at = CURRENT_TIMESTAMP WHERE ingested_at IS NULL")

        # chat_sessions
        if "user_id" not in _cols("chat_sessions"):
            self._connection.execute("ALTER TABLE chat_sessions ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default'")

        # learned_facts
        if "user_id" not in _cols("learned_facts"):
            self._connection.execute("ALTER TABLE learned_facts ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default'")

        # todos
        if "user_id" not in _cols("todos"):
            self._connection.execute("ALTER TABLE todos ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default'")

        # habits
        if "user_id" not in _cols("habits"):
            self._connection.execute("ALTER TABLE habits ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default'")

        # named_sessions
        if "user_id" not in _cols("named_sessions"):
            self._connection.execute("ALTER TABLE named_sessions ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default'")

        # whatsapp_sessions
        if "user_id" not in _cols("whatsapp_sessions"):
            self._connection.execute("ALTER TABLE whatsapp_sessions ADD COLUMN user_id TEXT NOT NULL DEFAULT ''")

        # chat_turns — backfill created_at for rows inserted before the column existed
        if "created_at" not in _cols("chat_turns"):
            self._connection.execute("ALTER TABLE chat_turns ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP")
        self._connection.execute(
            "UPDATE chat_turns SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"
        )

        # user_settings (new table — create if missing)
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id       TEXT NOT NULL,
                setting_key   TEXT NOT NULL,
                setting_value TEXT NOT NULL,
                updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, setting_key)
            )
        """)

        # hitl_requests (new table — create if missing)
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS hitl_requests (
                id              TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL DEFAULT 'default',
                session_id      TEXT,
                action_type     TEXT NOT NULL,
                action_payload  TEXT NOT NULL DEFAULT '{}',
                status          TEXT NOT NULL DEFAULT 'pending',
                created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
                expires_at      DATETIME DEFAULT (datetime('now', '+10 minutes')),
                resolved_at     DATETIME
            )
        """)

        # whatsapp_hitl_context — pending HITL approval awaiting yes/no from WhatsApp
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS whatsapp_hitl_context (
                phone_number TEXT PRIMARY KEY,
                hitl_id      TEXT NOT NULL,
                sent_at      DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # user_email_tokens — per-user Google OAuth tokens (Postgres parity).
        # account_type distinguishes scopes, e.g. 'personal' (Gmail) vs 'google_calendar'.
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS user_email_tokens (
                user_id      TEXT NOT NULL,
                account_type TEXT NOT NULL DEFAULT 'personal',
                token_json   TEXT NOT NULL,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, account_type)
            )
        """)

        # oauth_states — short-lived CSRF state tokens for the OAuth consent flow.
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS oauth_states (
                state      TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                expires_at DATETIME DEFAULT (datetime('now', '+10 minutes'))
            )
        """)

        # sage_calendar_events — provenance mirror of events Sage writes to Google Calendar.
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS sage_calendar_events (
                id              TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL,
                google_event_id TEXT,
                calendar_id     TEXT NOT NULL DEFAULT 'primary',
                plan_date       TEXT NOT NULL,
                title           TEXT NOT NULL,
                start_local     TEXT,
                end_local       TEXT,
                source_kind     TEXT,
                source_ref      TEXT,
                etag            TEXT,
                status          TEXT NOT NULL DEFAULT 'active',
                created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
                cancelled_at    DATETIME
            )
        """)
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_sage_calendar_events_lookup "
            "ON sage_calendar_events (user_id, plan_date, status)"
        )

        # pending_prompts — session-keyed multi-turn prompt state (generalizes nudge_context).
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS pending_prompts (
                session_id  TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                prompt_type TEXT NOT NULL,
                state_json  TEXT NOT NULL DEFAULT '{}',
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                expires_at  DATETIME
            )
        """)

    def close(self) -> None:
        self._connection.close()

    # ------------------------------------------------------------------
    # User management
    # ------------------------------------------------------------------

    def create_user(self, username: str, password: str) -> Dict[str, object]:
        user_id = str(uuid.uuid4())
        password_hash = _hash_password(password)
        self._connection.execute(
            "INSERT INTO users (user_id, username, password_hash) VALUES (?, ?, ?)",
            (user_id, username, password_hash),
        )
        self._connection.commit()
        return {"user_id": user_id, "username": username}

    def get_user_by_username(self, username: str) -> Optional[Dict[str, object]]:
        row = self._connection.execute(
            "SELECT user_id, username, password_hash, created_at FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        return dict(row) if row else None

    def verify_password(self, username: str, password: str) -> Optional[Dict[str, object]]:
        """Return user dict if credentials are valid, None otherwise."""
        user = self.get_user_by_username(username)
        if user and _verify_password(password, user["password_hash"]):
            return {"user_id": user["user_id"], "username": user["username"]}
        return None

    def delete_user_data(self, user_id: str) -> Dict[str, int]:
        """Delete a user's account and all rows owned by that user."""
        document_count = self._connection.execute(
            "SELECT COUNT(*) AS count FROM documents WHERE user_id = ?",
            (user_id,),
        ).fetchone()["count"]
        session_count = self._connection.execute(
            "SELECT COUNT(*) AS count FROM chat_sessions WHERE user_id = ?",
            (user_id,),
        ).fetchone()["count"]
        fact_count = self._connection.execute(
            "SELECT COUNT(*) AS count FROM learned_facts WHERE user_id = ?",
            (user_id,),
        ).fetchone()["count"]
        habit_count = self._connection.execute(
            "SELECT COUNT(*) AS count FROM habits WHERE user_id = ?",
            (user_id,),
        ).fetchone()["count"]
        todo_count = self._connection.execute(
            "SELECT COUNT(*) AS count FROM todos WHERE user_id = ?",
            (user_id,),
        ).fetchone()["count"]

        with self._connection:
            self._connection.execute(
                """
                DELETE FROM whatsapp_sessions
                WHERE session_id IN (
                    SELECT session_id FROM chat_sessions WHERE user_id = ?
                )
                """,
                (user_id,),
            )
            self._connection.execute(
                """
                DELETE FROM nudge_context
                WHERE habit_id IN (
                    SELECT id FROM habits WHERE user_id = ?
                )
                """,
                (user_id,),
            )
            self._connection.execute("DELETE FROM hitl_requests WHERE user_id = ?", (user_id,))
            self._connection.execute("DELETE FROM security_events WHERE user_id = ?", (user_id,))
            self._connection.execute("DELETE FROM user_settings WHERE user_id = ?", (user_id,))
            self._connection.execute("DELETE FROM todos WHERE user_id = ?", (user_id,))
            self._connection.execute("DELETE FROM learned_facts WHERE user_id = ?", (user_id,))
            self._connection.execute("DELETE FROM habits WHERE user_id = ?", (user_id,))
            self._connection.execute("DELETE FROM named_sessions WHERE user_id = ?", (user_id,))
            self._connection.execute("DELETE FROM chat_sessions WHERE user_id = ?", (user_id,))
            self._connection.execute("DELETE FROM documents WHERE user_id = ?", (user_id,))
            self._connection.execute("DELETE FROM users WHERE user_id = ?", (user_id,))

        return {
            "documents": int(document_count),
            "sessions": int(session_count),
            "facts": int(fact_count),
            "habits": int(habit_count),
            "todos": int(todo_count),
        }

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------

    def upsert_document(self, document_id: str, document: ParsedDocument, user_id: str = "") -> None:
        metadata_json = json.dumps(document.metadata, sort_keys=True)
        self._connection.execute(
            """
            INSERT INTO documents (
                document_id, user_id, source_path, file_name, file_type,
                checksum_sha256, parser_name, content_length, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                user_id,
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
                chunk_id, document_id, chunk_index, text, token_count,
                char_start, char_end, source_path, file_name,
                document_checksum_sha256, metadata_json
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
                chunk.chunk_id, chunk.document_id, chunk.chunk_index, chunk.text,
                chunk.token_count, chunk.char_start, chunk.char_end,
                chunk.source_path.as_posix(), chunk.file_name,
                chunk.document_checksum_sha256, metadata_json,
            ),
        )
        self._connection.commit()

    def get_document(self, document_id: str) -> Optional[Dict[str, object]]:
        row = self._connection.execute(
            "SELECT * FROM documents WHERE document_id = ?", (document_id,)
        ).fetchone()
        return self._row_to_dict(row)

    def get_chunk(self, chunk_id: str) -> Optional[Dict[str, object]]:
        row = self._connection.execute(
            "SELECT * FROM chunks WHERE chunk_id = ?", (chunk_id,)
        ).fetchone()
        return self._row_to_dict(row)

    def get_chunks_for_document(self, document_id: str) -> List[Dict[str, object]]:
        rows = self._connection.execute(
            "SELECT * FROM chunks WHERE document_id = ? ORDER BY chunk_index ASC", (document_id,)
        ).fetchall()
        return [self._row_to_dict(row) for row in rows if row is not None]

    def set_document_source(self, document_id: str, source_type: str, source_url: Optional[str] = None) -> None:
        self._connection.execute(
            "UPDATE documents SET source_type = ?, source_url = ?, ingested_at = CURRENT_TIMESTAMP WHERE document_id = ?",
            (source_type, source_url, document_id),
        )
        self._connection.commit()

    def is_url_ingested(self, source_url: str, user_id: str = "") -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM documents WHERE source_url = ? AND source_type = 'url' AND user_id = ? LIMIT 1",
            (source_url, user_id),
        ).fetchone()
        return row is not None

    def list_url_sources(self, user_id: str = "") -> List[Dict[str, object]]:
        rows = self._connection.execute(
            "SELECT document_id, file_name, source_url, ingested_at FROM documents WHERE source_type = 'url' AND user_id = ? ORDER BY ingested_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_all_sources(self, user_id: str = "") -> List[Dict[str, object]]:
        rows = self._connection.execute(
            "SELECT document_id, file_name, source_path, source_type, source_url, ingested_at FROM documents WHERE user_id = ? ORDER BY ingested_at DESC, created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def create_session(self, session_id: str, title: str = "", user_id: str = "") -> None:
        self._connection.execute(
            "INSERT OR IGNORE INTO chat_sessions (session_id, user_id, title) VALUES (?, ?, ?)",
            (session_id, user_id, title),
        )
        self._connection.commit()

    def append_turn(self, session_id: str, turn_id: str, role: str, content: str, turn_index: int) -> None:
        self._connection.execute(
            "INSERT INTO chat_turns (turn_id, session_id, role, content, turn_index) VALUES (?, ?, ?, ?, ?)",
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

    def list_sessions(self, limit: int = 20, user_id: str = "") -> List[Dict[str, object]]:
        rows = self._connection.execute(
            "SELECT session_id, title, created_at, updated_at FROM chat_sessions WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def update_session_title(self, session_id: str, title: str) -> None:
        self._connection.execute(
            "UPDATE chat_sessions SET title = ? WHERE session_id = ?", (title, session_id)
        )
        self._connection.commit()

    def delete_session(self, session_id: str) -> None:
        self._connection.execute("DELETE FROM chat_turns WHERE session_id = ?", (session_id,))
        self._connection.execute("DELETE FROM chat_sessions WHERE session_id = ?", (session_id,))
        self._connection.commit()

    def get_or_create_named_session(self, name: str, user_id: str = "") -> str:
        row = self._connection.execute(
            "SELECT session_id FROM named_sessions WHERE name = ? AND user_id = ?",
            (name, user_id),
        ).fetchone()
        if row:
            return row["session_id"]
        session_id = str(uuid.uuid4())
        self._connection.execute(
            "INSERT INTO named_sessions (name, user_id, session_id) VALUES (?, ?, ?)",
            (name, user_id, session_id),
        )
        self.create_session(session_id=session_id, title=name, user_id=user_id)
        self._connection.commit()
        return session_id

    # ------------------------------------------------------------------
    # Facts
    # ------------------------------------------------------------------

    def insert_fact(self, fact_id: str, content: str, category: str, source: str = "user", confidence_score: float = 1.0, *, user_id: str) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO learned_facts (fact_id, user_id, content, category, source, confidence_score) VALUES (?, ?, ?, ?, ?, ?)",
            (fact_id, user_id, content, category, source, confidence_score),
        )
        self._connection.commit()

    def list_facts(self, category: Optional[str] = None, *, user_id: str) -> List[Dict[str, object]]:
        if category:
            rows = self._connection.execute(
                "SELECT fact_id, content, category, source, confidence_score, created_at, usage_count FROM learned_facts WHERE user_id = ? AND category = ? ORDER BY created_at DESC",
                (user_id, category),
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT fact_id, content, category, source, confidence_score, created_at, usage_count FROM learned_facts WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_document(self, document_id: str, user_id: str) -> None:
        self._connection.execute(
            "DELETE FROM chunks WHERE document_id = ?", (document_id,)
        )
        self._connection.execute(
            "DELETE FROM documents WHERE document_id = ? AND user_id = ?", (document_id, user_id)
        )
        self._connection.commit()

    def delete_fact(self, fact_id: str, user_id: str) -> None:
        self._connection.execute(
            "DELETE FROM learned_facts WHERE fact_id = ? AND user_id = ?", (fact_id, user_id)
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

    # ------------------------------------------------------------------
    # Todos
    # ------------------------------------------------------------------

    def create_todo(self, title: str, list_name: Optional[str] = None, due_at: Optional[datetime] = None, user_id: str = "") -> Dict[str, object]:
        todo_id = str(uuid.uuid4())
        self._connection.execute(
            "INSERT INTO todos (id, user_id, title, list_name, due_at) VALUES (?, ?, ?, ?, ?)",
            (todo_id, user_id, title, list_name, self._format_datetime(due_at)),
        )
        self._connection.commit()
        todo = self.get_todo(todo_id)
        if todo is None:
            raise RuntimeError("Created todo could not be read back from SQLite")
        return todo

    def get_todo(self, todo_id: str) -> Optional[Dict[str, object]]:
        row = self._connection.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
        return self._row_to_dict(row)

    def get_pending_todos(self, user_id: str = "") -> List[Dict[str, object]]:
        now = self._format_datetime(datetime.now())
        rows = self._connection.execute(
            """
            SELECT * FROM todos
            WHERE user_id = ? AND due_at IS NOT NULL AND due_at > ?
              AND completed_at IS NULL AND notified_at IS NULL
            ORDER BY due_at ASC
            """,
            (user_id, now),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_todos_due_soon(self, minutes_ahead: int = 60, user_id: str = "") -> List[Dict[str, object]]:
        now = datetime.now()
        cutoff = now + timedelta(minutes=minutes_ahead)
        rows = self._connection.execute(
            """
            SELECT * FROM todos
            WHERE user_id = ? AND due_at IS NOT NULL AND due_at <= ?
              AND completed_at IS NULL AND notified_at IS NULL
            ORDER BY due_at ASC
            """,
            (user_id, self._format_datetime(cutoff)),
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

    def list_todos(self, user_id: str = "") -> List[Dict[str, object]]:
        rows = self._connection.execute(
            """
            SELECT * FROM todos
            WHERE user_id = ? AND completed_at IS NULL
            ORDER BY
                CASE WHEN due_at IS NULL THEN 1 ELSE 0 END,
                due_at ASC,
                created_at DESC
            """,
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_todo(self, todo_id: str, user_id: str = "") -> bool:
        cur = self._connection.execute(
            "DELETE FROM todos WHERE id = ? AND user_id = ?",
            (todo_id, user_id),
        )
        self._connection.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # WhatsApp
    # ------------------------------------------------------------------

    def get_or_create_whatsapp_session(self, phone_number: str, user_id: str = "") -> str:
        row = self._connection.execute(
            "SELECT session_id FROM whatsapp_sessions WHERE phone_number = ?", (phone_number,)
        ).fetchone()
        if row:
            return row["session_id"]
        session_id = str(uuid.uuid4())
        self._connection.execute(
            "INSERT INTO whatsapp_sessions (phone_number, session_id, user_id) VALUES (?, ?, ?)",
            (phone_number, session_id, user_id),
        )
        self.create_session(session_id=session_id, title=f"WhatsApp {phone_number}", user_id=user_id)
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
            ON CONFLICT(usage_date) DO UPDATE SET sent_count = sent_count + 1, updated_at = CURRENT_TIMESTAMP
            """,
            (day,),
        )
        row = self._connection.execute(
            "SELECT sent_count FROM whatsapp_usage_daily WHERE usage_date = ?", (day,)
        ).fetchone()
        self._connection.commit()
        return int(row["sent_count"])

    def get_whatsapp_usage_today(self, daily_limit: int = 50) -> Dict[str, object]:
        day = date.today().isoformat()
        row = self._connection.execute(
            "SELECT sent_count FROM whatsapp_usage_daily WHERE usage_date = ?", (day,)
        ).fetchone()
        sent_count = int(row["sent_count"]) if row else 0
        return {"date": day, "sent_count": sent_count, "daily_limit": daily_limit, "remaining": max(daily_limit - sent_count, 0)}

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
            WHERE ct.role = 'user' AND DATE(ct.created_at) = ?
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
            "SELECT 1 FROM whatsapp_usage_alerts WHERE usage_date = ? AND threshold = ?", (day, threshold)
        ).fetchone()
        return row is not None

    def mark_whatsapp_usage_alert_sent(self, threshold: int, usage_date: Optional[date] = None) -> None:
        day = (usage_date or date.today()).isoformat()
        self._connection.execute(
            "INSERT OR IGNORE INTO whatsapp_usage_alerts (usage_date, threshold) VALUES (?, ?)", (day, threshold)
        )
        self._connection.commit()

    # ------------------------------------------------------------------
    # Nudge context
    # ------------------------------------------------------------------

    def set_nudge_context(self, phone_number: str, habit_id: str) -> None:
        self._connection.execute(
            """
            INSERT INTO nudge_context (phone_number, habit_id, sent_at)
            VALUES (?, ?, ?)
            ON CONFLICT(phone_number) DO UPDATE SET habit_id = excluded.habit_id, sent_at = excluded.sent_at
            """,
            (phone_number, habit_id, self._format_datetime(datetime.now())),
        )
        self._connection.commit()

    def get_nudge_context(self, phone_number: str) -> Optional[str]:
        expires_after = self._format_datetime(datetime.now() - timedelta(hours=24))
        row = self._connection.execute(
            "SELECT habit_id FROM nudge_context WHERE phone_number = ? AND sent_at >= ?",
            (phone_number, expires_after),
        ).fetchone()
        return row["habit_id"] if row else None

    def clear_nudge_context(self, phone_number: str) -> None:
        self._connection.execute("DELETE FROM nudge_context WHERE phone_number = ?", (phone_number,))
        self._connection.commit()

    def set_whatsapp_hitl_context(self, phone_number: str, hitl_id: str) -> None:
        self._connection.execute(
            """
            INSERT INTO whatsapp_hitl_context (phone_number, hitl_id, sent_at)
            VALUES (?, ?, ?)
            ON CONFLICT(phone_number) DO UPDATE SET hitl_id = excluded.hitl_id, sent_at = excluded.sent_at
            """,
            (phone_number, hitl_id, self._format_datetime(datetime.now())),
        )
        self._connection.commit()

    def get_whatsapp_hitl_context(self, phone_number: str) -> Optional[str]:
        expires_after = self._format_datetime(datetime.now() - timedelta(minutes=10))
        row = self._connection.execute(
            "SELECT hitl_id FROM whatsapp_hitl_context WHERE phone_number = ? AND sent_at >= ?",
            (phone_number, expires_after),
        ).fetchone()
        return row["hitl_id"] if row else None

    def clear_whatsapp_hitl_context(self, phone_number: str) -> None:
        self._connection.execute(
            "DELETE FROM whatsapp_hitl_context WHERE phone_number = ?", (phone_number,)
        )
        self._connection.commit()

    # ------------------------------------------------------------------
    # User settings
    # ------------------------------------------------------------------

    def get_user_setting(self, user_id: str, key: str) -> Optional[str]:
        row = self._connection.execute(
            "SELECT setting_value FROM user_settings WHERE user_id = ? AND setting_key = ?",
            (user_id, key),
        ).fetchone()
        return row["setting_value"] if row else None

    def set_user_setting(self, user_id: str, key: str, value: str) -> None:
        self._connection.execute(
            """
            INSERT INTO user_settings (user_id, setting_key, setting_value)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, setting_key) DO UPDATE SET
                setting_value = excluded.setting_value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, key, value),
        )
        self._connection.commit()

    def delete_user_setting(self, user_id: str, key: str) -> None:
        self._connection.execute(
            "DELETE FROM user_settings WHERE user_id = ? AND setting_key = ?",
            (user_id, key),
        )
        self._connection.commit()

    # ------------------------------------------------------------------
    # Google OAuth tokens (Postgres parity)
    # ------------------------------------------------------------------

    def get_email_token(self, user_id: str, account_type: str = "personal") -> Optional[Dict]:
        row = self._connection.execute(
            "SELECT token_json FROM user_email_tokens WHERE user_id = ? AND account_type = ?",
            (user_id, account_type),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["token_json"])

    def upsert_email_token(self, user_id: str, token_json: dict, account_type: str = "personal") -> None:
        self._connection.execute(
            """
            INSERT INTO user_email_tokens (user_id, account_type, token_json, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, account_type) DO UPDATE SET
                token_json = excluded.token_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, account_type, json.dumps(token_json)),
        )
        self._connection.commit()

    def delete_email_token(self, user_id: str, account_type: str = "personal") -> None:
        self._connection.execute(
            "DELETE FROM user_email_tokens WHERE user_id = ? AND account_type = ?",
            (user_id, account_type),
        )
        self._connection.commit()

    def has_email_token(self, user_id: str, account_type: str = "personal") -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM user_email_tokens WHERE user_id = ? AND account_type = ?",
            (user_id, account_type),
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # OAuth state (CSRF token for the OAuth consent flow)
    # ------------------------------------------------------------------

    def store_oauth_state(self, state: str, user_id: str) -> None:
        # Set expiry explicitly (local-naive) so comparisons stay consistent with
        # _format_datetime elsewhere, rather than relying on SQLite's UTC default.
        now = datetime.now()
        self._connection.execute(
            "DELETE FROM oauth_states WHERE expires_at < ?",
            (self._format_datetime(now),),
        )
        self._connection.execute(
            """
            INSERT INTO oauth_states (state, user_id, expires_at) VALUES (?, ?, ?)
            ON CONFLICT(state) DO NOTHING
            """,
            (state, user_id, self._format_datetime(now + timedelta(minutes=10))),
        )
        self._connection.commit()

    def pop_oauth_state(self, state: str) -> Optional[str]:
        """Validate and consume a state token. Returns user_id or None if invalid/expired."""
        now = self._format_datetime(datetime.now())
        row = self._connection.execute(
            "SELECT user_id FROM oauth_states WHERE state = ? AND expires_at > ?",
            (state, now),
        ).fetchone()
        self._connection.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
        self._connection.commit()
        return row["user_id"] if row else None

    # ------------------------------------------------------------------
    # Sage calendar events (provenance mirror for the daily planner)
    # ------------------------------------------------------------------

    def insert_calendar_event(self, row: Dict[str, object]) -> None:
        self._connection.execute(
            """
            INSERT INTO sage_calendar_events
                (id, user_id, google_event_id, calendar_id, plan_date, title,
                 start_local, end_local, source_kind, source_ref, etag, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"], row["user_id"], row.get("google_event_id"),
                row.get("calendar_id", "primary"), row["plan_date"], row["title"],
                row.get("start_local"), row.get("end_local"), row.get("source_kind"),
                row.get("source_ref"), row.get("etag"), row.get("status", "active"),
            ),
        )
        self._connection.commit()

    def set_calendar_event_google_id(self, event_id: str, google_event_id: str, etag: Optional[str]) -> None:
        self._connection.execute(
            "UPDATE sage_calendar_events SET google_event_id = ?, etag = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (google_event_id, etag, event_id),
        )
        self._connection.commit()

    def update_calendar_event(
        self, event_id: str, *, title: Optional[str] = None,
        start_local: Optional[str] = None, end_local: Optional[str] = None, etag: Optional[str] = None,
    ) -> None:
        self._connection.execute(
            """
            UPDATE sage_calendar_events
            SET title = COALESCE(?, title),
                start_local = COALESCE(?, start_local),
                end_local = COALESCE(?, end_local),
                etag = COALESCE(?, etag),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (title, start_local, end_local, etag, event_id),
        )
        self._connection.commit()

    def soft_cancel_calendar_event(self, event_id: str) -> None:
        self._connection.execute(
            "UPDATE sage_calendar_events SET status = 'cancelled', cancelled_at = CURRENT_TIMESTAMP, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (event_id,),
        )
        self._connection.commit()

    def list_managed_calendar_events(
        self, user_id: str, plan_date: str, status: str = "active"
    ) -> List[Dict[str, object]]:
        rows = self._connection.execute(
            "SELECT * FROM sage_calendar_events WHERE user_id = ? AND plan_date = ? AND status = ? "
            "ORDER BY start_local ASC",
            (user_id, plan_date, status),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_calendar_event(self, event_id: str) -> Optional[Dict[str, object]]:
        row = self._connection.execute(
            "SELECT * FROM sage_calendar_events WHERE id = ?", (event_id,)
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Pending prompts (session-keyed multi-turn state)
    # ------------------------------------------------------------------

    def set_pending_prompt(
        self, session_id: str, user_id: str, prompt_type: str,
        state: Dict[str, object], ttl_minutes: int = 30,
    ) -> None:
        expires_at = self._format_datetime(datetime.now() + timedelta(minutes=ttl_minutes))
        self._connection.execute(
            """
            INSERT INTO pending_prompts (session_id, user_id, prompt_type, state_json, expires_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                user_id = excluded.user_id,
                prompt_type = excluded.prompt_type,
                state_json = excluded.state_json,
                created_at = CURRENT_TIMESTAMP,
                expires_at = excluded.expires_at
            """,
            (session_id, user_id, prompt_type, json.dumps(state), expires_at),
        )
        self._connection.commit()

    def get_pending_prompt(self, session_id: str) -> Optional[Dict[str, object]]:
        now = self._format_datetime(datetime.now())
        row = self._connection.execute(
            "SELECT * FROM pending_prompts WHERE session_id = ? AND (expires_at IS NULL OR expires_at > ?)",
            (session_id, now),
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["state"] = json.loads(data.get("state_json") or "{}")
        return data

    def clear_pending_prompt(self, session_id: str) -> None:
        self._connection.execute(
            "DELETE FROM pending_prompts WHERE session_id = ?", (session_id,)
        )
        self._connection.commit()

    def append_pending_prompt_note(self, session_id: str, note: str) -> None:
        current = self.get_pending_prompt(session_id)
        if current is None:
            return
        state = current["state"]
        notes = state.get("gathered_notes") or []
        notes.append(note)
        state["gathered_notes"] = notes
        self.set_pending_prompt(session_id, current["user_id"], current["prompt_type"], state)

    def log_security_event(self, event_id: str, user_id: str, event_type: str, severity: str, snippet: str) -> None:
        self._connection.execute(
            """
            INSERT INTO security_events
                (event_id, user_id, event_type, severity, snippet)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event_id, user_id, event_type, severity, snippet),
        )
        self._connection.commit()

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # HITL requests
    # ------------------------------------------------------------------

    def create_hitl_request(
        self,
        id: str,
        user_id: str,
        action_type: str,
        action_payload: dict,
        session_id: Optional[str] = None,
    ) -> str:
        self._connection.execute(
            """
            INSERT INTO hitl_requests (id, user_id, session_id, action_type, action_payload)
            VALUES (?, ?, ?, ?, ?)
            """,
            (id, user_id, session_id, action_type, json.dumps(action_payload)),
        )
        self._connection.commit()
        return id

    def get_hitl_request(self, id: str) -> Optional[Dict[str, object]]:
        row = self._connection.execute(
            "SELECT * FROM hitl_requests WHERE id = ?", (id,)
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        if isinstance(data.get("action_payload"), str):
            data["action_payload"] = json.loads(data["action_payload"])
        # Parse expires_at into a timezone-aware datetime for expiry comparison
        from datetime import timezone
        expires_raw = data.get("expires_at")
        if isinstance(expires_raw, str):
            from datetime import datetime as _dt
            try:
                data["expires_at"] = _dt.fromisoformat(expires_raw).replace(tzinfo=timezone.utc)
            except ValueError:
                data["expires_at"] = None
        return data

    def attach_hitl_context(self, id: str, context: Dict[str, object]) -> None:
        row = self.get_hitl_request(id)
        if not row:
            return
        payload = row.get("action_payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        payload["__hitl_context"] = context
        self._connection.execute(
            "UPDATE hitl_requests SET action_payload = ? WHERE id = ?",
            (json.dumps(payload), id),
        )
        self._connection.commit()

    def resolve_hitl_request(self, id: str, status: str) -> None:
        self._connection.execute(
            "UPDATE hitl_requests SET status = ?, resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, id),
        )
        self._connection.commit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
        if value.tzinfo is not None:
            value = value.astimezone().replace(tzinfo=None)
        return value.replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
