import binascii
import hashlib
import json
import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import unquote, urlparse

import psycopg2
import psycopg2.extras
from psycopg2.extras import RealDictCursor


def _connect(database_url: str) -> psycopg2.extensions.connection:
    """Connect using parsed URL components so special chars in passwords work correctly."""
    p = urlparse(database_url)
    kwargs: Dict[str, object] = {
        "host": p.hostname,
        "port": p.port or 5432,
        "dbname": p.path.lstrip("/"),
        "user": unquote(p.username) if p.username else "",
        "password": unquote(p.password) if p.password else "",
        "cursor_factory": RealDictCursor,
    }
    # Pass through query-string params (e.g. sslmode=require)
    if p.query:
        for part in p.query.split("&"):
            k, _, v = part.partition("=")
            if k:
                kwargs[k] = v
    return psycopg2.connect(**kwargs)

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


class PostgresRegistry:
    """Drop-in PostgreSQL replacement for SQLiteRegistry. Uses DATABASE_URL."""

    def __init__(self, database_url: str):
        self._database_url = database_url
        self._conn = _connect(database_url)
        self._conn.autocommit = False

    def _cursor(self):
        try:
            self._conn.isolation_level  # ping
        except psycopg2.InterfaceError:
            self._conn = _connect(self._database_url)
            self._conn.autocommit = False
        return self._conn.cursor()

    def _commit(self):
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # User management
    # ------------------------------------------------------------------

    def create_user(self, username: str, password: str) -> Dict[str, object]:
        user_id = str(uuid.uuid4())
        password_hash = _hash_password(password)
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO users (user_id, username, password_hash) VALUES (%s, %s, %s)",
                (user_id, username, password_hash),
            )
        self._commit()
        return {"user_id": user_id, "username": username}

    def get_user_by_username(self, username: str) -> Optional[Dict[str, object]]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT user_id, username, password_hash, created_at FROM users WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()
        return dict(row) if row else None

    def verify_password(self, username: str, password: str) -> Optional[Dict[str, object]]:
        user = self.get_user_by_username(username)
        if user and _verify_password(password, user["password_hash"]):
            return {"user_id": user["user_id"], "username": user["username"]}
        return None

    def delete_user_data(self, user_id: str) -> Dict[str, int]:
        """Delete a user's account and all rows owned by that user."""
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM documents WHERE user_id = %s) AS documents,
                    (SELECT COUNT(*) FROM chat_sessions WHERE user_id = %s) AS sessions,
                    (SELECT COUNT(*) FROM learned_facts WHERE user_id = %s) AS facts,
                    (SELECT COUNT(*) FROM habits WHERE user_id = %s) AS habits,
                    (SELECT COUNT(*) FROM todos WHERE user_id = %s) AS todos
                """,
                (user_id, user_id, user_id, user_id, user_id),
            )
            counts = dict(cur.fetchone() or {})

            cur.execute(
                """
                DELETE FROM whatsapp_sessions
                WHERE session_id IN (
                    SELECT session_id FROM chat_sessions WHERE user_id = %s
                )
                """,
                (user_id,),
            )
            cur.execute(
                """
                DELETE FROM nudge_context
                WHERE habit_id IN (
                    SELECT id FROM habits WHERE user_id = %s
                )
                """,
                (user_id,),
            )
            cur.execute("DELETE FROM hitl_requests WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM security_events WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM user_settings WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM todos WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM learned_facts WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM habits WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM named_sessions WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM chat_sessions WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM documents WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM users WHERE user_id = %s", (user_id,))

        self._commit()
        return {
            "documents": int(counts.get("documents", 0)),
            "sessions": int(counts.get("sessions", 0)),
            "facts": int(counts.get("facts", 0)),
            "habits": int(counts.get("habits", 0)),
            "todos": int(counts.get("todos", 0)),
        }

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------

    def upsert_document(self, document_id: str, document: ParsedDocument, user_id: str) -> None:
        metadata_json = json.dumps(document.metadata, sort_keys=True)
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO documents (
                    document_id, user_id, source_path, file_name, file_type,
                    checksum_sha256, parser_name, content_length, metadata_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (document_id) DO UPDATE SET
                    source_path     = EXCLUDED.source_path,
                    file_name       = EXCLUDED.file_name,
                    file_type       = EXCLUDED.file_type,
                    checksum_sha256 = EXCLUDED.checksum_sha256,
                    parser_name     = EXCLUDED.parser_name,
                    content_length  = EXCLUDED.content_length,
                    metadata_json   = EXCLUDED.metadata_json,
                    updated_at      = NOW()
                """,
                (
                    document_id, user_id, document.source_path.as_posix(),
                    document.filename, document.extension, document.checksum_sha256,
                    document.parser_name, document.char_count, metadata_json,
                ),
            )
        self._commit()

    def upsert_chunk(self, chunk: DocumentChunk) -> None:
        metadata_json = json.dumps(chunk.metadata, sort_keys=True)
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO chunks (
                    chunk_id, document_id, chunk_index, text, token_count,
                    char_start, char_end, source_path, file_name,
                    document_checksum_sha256, metadata_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (chunk_id) DO UPDATE SET
                    document_id              = EXCLUDED.document_id,
                    chunk_index              = EXCLUDED.chunk_index,
                    text                     = EXCLUDED.text,
                    token_count              = EXCLUDED.token_count,
                    char_start               = EXCLUDED.char_start,
                    char_end                 = EXCLUDED.char_end,
                    source_path              = EXCLUDED.source_path,
                    file_name                = EXCLUDED.file_name,
                    document_checksum_sha256 = EXCLUDED.document_checksum_sha256,
                    metadata_json            = EXCLUDED.metadata_json,
                    updated_at               = NOW()
                """,
                (
                    chunk.chunk_id, chunk.document_id, chunk.chunk_index, chunk.text,
                    chunk.token_count, chunk.char_start, chunk.char_end,
                    chunk.source_path.as_posix(), chunk.file_name,
                    chunk.document_checksum_sha256, metadata_json,
                ),
            )
        self._commit()

    def get_document(self, document_id: str) -> Optional[Dict[str, object]]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM documents WHERE document_id = %s", (document_id,))
            row = cur.fetchone()
        return self._row_to_dict(row)

    def get_chunk(self, chunk_id: str) -> Optional[Dict[str, object]]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM chunks WHERE chunk_id = %s", (chunk_id,))
            row = cur.fetchone()
        return self._row_to_dict(row)

    def get_chunks_for_document(self, document_id: str) -> List[Dict[str, object]]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM chunks WHERE document_id = %s ORDER BY chunk_index ASC", (document_id,)
            )
            rows = cur.fetchall()
        return [self._row_to_dict(r) for r in rows if r is not None]

    def set_document_source(self, document_id: str, source_type: str, source_url: Optional[str] = None) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE documents SET source_type = %s, source_url = %s, ingested_at = NOW() WHERE document_id = %s",
                (source_type, source_url, document_id),
            )
        self._commit()

    def is_url_ingested(self, source_url: str, user_id: str) -> bool:
        with self._cursor() as cur:
            cur.execute(
                "SELECT 1 FROM documents WHERE source_url = %s AND source_type = 'url' AND user_id = %s LIMIT 1",
                (source_url, user_id),
            )
            return cur.fetchone() is not None

    def list_url_sources(self, user_id: str) -> List[Dict[str, object]]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT document_id, file_name, source_url, ingested_at FROM documents WHERE source_type = 'url' AND user_id = %s ORDER BY ingested_at DESC",
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def list_all_sources(self, user_id: str) -> List[Dict[str, object]]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT document_id, file_name, source_path, source_type, source_url, ingested_at FROM documents WHERE user_id = %s ORDER BY ingested_at DESC, created_at DESC",
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def create_session(self, session_id: str, title: str = "", user_id: str = "") -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO chat_sessions (session_id, user_id, title) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (session_id, user_id, title),
            )
        self._commit()

    def append_turn(self, session_id: str, turn_id: str, role: str, content: str, turn_index: int) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO chat_turns (turn_id, session_id, role, content, turn_index) VALUES (%s, %s, %s, %s, %s)",
                (turn_id, session_id, role, content, turn_index),
            )
            cur.execute(
                "UPDATE chat_sessions SET updated_at = NOW() WHERE session_id = %s", (session_id,)
            )
        self._commit()

    def get_session_turns(self, session_id: str) -> List[Dict[str, object]]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT turn_id, session_id, role, content, turn_index, created_at FROM chat_turns WHERE session_id = %s ORDER BY turn_index ASC",
                (session_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def list_sessions(self, limit: int = 20, user_id: str = "") -> List[Dict[str, object]]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT session_id, title, created_at, updated_at FROM chat_sessions WHERE user_id = %s ORDER BY updated_at DESC LIMIT %s",
                (user_id, limit),
            )
            return [dict(r) for r in cur.fetchall()]

    def update_session_title(self, session_id: str, title: str) -> None:
        with self._cursor() as cur:
            cur.execute("UPDATE chat_sessions SET title = %s WHERE session_id = %s", (title, session_id))
        self._commit()

    def delete_session(self, session_id: str) -> None:
        with self._cursor() as cur:
            cur.execute("DELETE FROM chat_turns WHERE session_id = %s", (session_id,))
            cur.execute("DELETE FROM chat_sessions WHERE session_id = %s", (session_id,))
        self._commit()

    def get_or_create_named_session(self, name: str, user_id: str) -> str:
        with self._cursor() as cur:
            cur.execute(
                "SELECT session_id FROM named_sessions WHERE name = %s AND user_id = %s",
                (name, user_id),
            )
            row = cur.fetchone()
        if row:
            return row["session_id"]
        session_id = str(uuid.uuid4())
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO named_sessions (name, user_id, session_id) VALUES (%s, %s, %s)",
                (name, user_id, session_id),
            )
        self._commit()
        self.create_session(session_id=session_id, title=name, user_id=user_id)
        return session_id

    # ------------------------------------------------------------------
    # Facts
    # ------------------------------------------------------------------

    def insert_fact(self, fact_id: str, content: str, category: str, source: str = "user", confidence_score: float = 1.0, *, user_id: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO learned_facts (fact_id, user_id, content, category, source, confidence_score)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (fact_id) DO UPDATE SET
                    content          = EXCLUDED.content,
                    category         = EXCLUDED.category,
                    source           = EXCLUDED.source,
                    confidence_score = EXCLUDED.confidence_score
                """,
                (fact_id, user_id, content, category, source, confidence_score),
            )
        self._commit()

    def list_facts(self, category: Optional[str] = None, *, user_id: str) -> List[Dict[str, object]]:
        with self._cursor() as cur:
            if category:
                cur.execute(
                    "SELECT fact_id, content, category, source, confidence_score, created_at, usage_count FROM learned_facts WHERE user_id = %s AND category = %s ORDER BY created_at DESC",
                    (user_id, category),
                )
            else:
                cur.execute(
                    "SELECT fact_id, content, category, source, confidence_score, created_at, usage_count FROM learned_facts WHERE user_id = %s ORDER BY created_at DESC",
                    (user_id,),
                )
            return [dict(r) for r in cur.fetchall()]

    def delete_fact(self, fact_id: str, user_id: str) -> None:
        with self._cursor() as cur:
            cur.execute("DELETE FROM learned_facts WHERE fact_id = %s AND user_id = %s", (fact_id, user_id))
        self._commit()

    def get_fact(self, fact_id: str) -> Optional[Dict[str, object]]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT fact_id, content, category, source, confidence_score, created_at, usage_count FROM learned_facts WHERE fact_id = %s",
                (fact_id,),
            )
            row = cur.fetchone()
        return dict(row) if row else None

    def increment_fact_usage(self, fact_id: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE learned_facts SET usage_count = usage_count + 1, last_used_at = NOW() WHERE fact_id = %s",
                (fact_id,),
            )
        self._commit()

    # ------------------------------------------------------------------
    # Todos
    # ------------------------------------------------------------------

    def create_todo(self, title: str, list_name: Optional[str] = None, due_at: Optional[datetime] = None, user_id: str = "") -> Dict[str, object]:
        todo_id = str(uuid.uuid4())
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO todos (id, user_id, title, list_name, due_at) VALUES (%s, %s, %s, %s, %s)",
                (todo_id, user_id, title, list_name, due_at),
            )
        self._commit()
        todo = self.get_todo(todo_id)
        if todo is None:
            raise RuntimeError("Created todo could not be read back from Postgres")
        return todo

    def get_todo(self, todo_id: str) -> Optional[Dict[str, object]]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM todos WHERE id = %s", (todo_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    def get_pending_todos(self, user_id: str = "") -> List[Dict[str, object]]:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT * FROM todos
                WHERE user_id = %s AND due_at IS NOT NULL AND due_at > NOW()
                  AND completed_at IS NULL AND notified_at IS NULL
                ORDER BY due_at ASC
                """,
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_todos_due_soon(self, minutes_ahead: int = 60, user_id: str = "") -> List[Dict[str, object]]:
        cutoff = datetime.now() + timedelta(minutes=minutes_ahead)
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT * FROM todos
                WHERE user_id = %s AND due_at IS NOT NULL AND due_at <= %s
                  AND completed_at IS NULL AND notified_at IS NULL
                ORDER BY due_at ASC
                """,
                (user_id, cutoff),
            )
            return [dict(r) for r in cur.fetchall()]

    def mark_todo_notified(self, todo_id: str) -> None:
        with self._cursor() as cur:
            cur.execute("UPDATE todos SET notified_at = NOW() WHERE id = %s", (todo_id,))
        self._commit()

    def mark_todo_completed(self, todo_id: str) -> None:
        with self._cursor() as cur:
            cur.execute("UPDATE todos SET completed_at = NOW() WHERE id = %s", (todo_id,))
        self._commit()

    # ------------------------------------------------------------------
    # WhatsApp
    # ------------------------------------------------------------------

    def get_or_create_whatsapp_session(self, phone_number: str) -> str:
        with self._cursor() as cur:
            cur.execute("SELECT session_id FROM whatsapp_sessions WHERE phone_number = %s", (phone_number,))
            row = cur.fetchone()
        if row:
            return row["session_id"]
        session_id = str(uuid.uuid4())
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO whatsapp_sessions (phone_number, session_id) VALUES (%s, %s)",
                (phone_number, session_id),
            )
        self._commit()
        self.create_session(session_id=session_id, title=f"WhatsApp {phone_number}")
        return session_id

    def update_whatsapp_last_active(self, phone_number: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE whatsapp_sessions SET last_active = NOW() WHERE phone_number = %s", (phone_number,)
            )
        self._commit()

    def record_whatsapp_message_sent(self, usage_date: Optional[date] = None) -> int:
        day = (usage_date or date.today()).isoformat()
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO whatsapp_usage_daily (usage_date, sent_count, updated_at)
                VALUES (%s, 1, NOW())
                ON CONFLICT (usage_date) DO UPDATE SET sent_count = whatsapp_usage_daily.sent_count + 1, updated_at = NOW()
                """,
                (day,),
            )
            cur.execute("SELECT sent_count FROM whatsapp_usage_daily WHERE usage_date = %s", (day,))
            row = cur.fetchone()
        self._commit()
        return int(row["sent_count"])

    def get_whatsapp_usage_today(self, daily_limit: int = 50) -> Dict[str, object]:
        day = date.today().isoformat()
        with self._cursor() as cur:
            cur.execute("SELECT sent_count FROM whatsapp_usage_daily WHERE usage_date = %s", (day,))
            row = cur.fetchone()
        sent_count = int(row["sent_count"]) if row else 0
        return {"date": day, "sent_count": sent_count, "daily_limit": daily_limit, "remaining": max(daily_limit - sent_count, 0)}

    def get_chat_usage_today(self) -> Dict[str, object]:
        day = date.today().isoformat()
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT
                    CASE
                        WHEN ws.session_id IS NOT NULL THEN 'whatsapp'
                        WHEN ns.name LIKE 'cli:%%' THEN 'cli'
                        ELSE 'other'
                    END AS source,
                    COUNT(*) AS count
                FROM chat_turns ct
                LEFT JOIN whatsapp_sessions ws ON ws.session_id = ct.session_id
                LEFT JOIN named_sessions ns ON ns.session_id = ct.session_id
                WHERE ct.role = 'user' AND ct.created_at::date = %s::date
                GROUP BY source
                """,
                (day,),
            )
            rows = cur.fetchall()
        counts: Dict[str, object] = {"cli": 0, "whatsapp": 0, "other": 0}
        for row in rows:
            counts[row["source"]] = int(row["count"])
        counts["date"] = day
        counts["total"] = int(counts["cli"]) + int(counts["whatsapp"]) + int(counts["other"])
        return counts

    def has_whatsapp_usage_alert_sent(self, threshold: int, usage_date: Optional[date] = None) -> bool:
        day = (usage_date or date.today()).isoformat()
        with self._cursor() as cur:
            cur.execute(
                "SELECT 1 FROM whatsapp_usage_alerts WHERE usage_date = %s AND threshold = %s", (day, threshold)
            )
            return cur.fetchone() is not None

    def mark_whatsapp_usage_alert_sent(self, threshold: int, usage_date: Optional[date] = None) -> None:
        day = (usage_date or date.today()).isoformat()
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO whatsapp_usage_alerts (usage_date, threshold) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (day, threshold),
            )
        self._commit()

    # ------------------------------------------------------------------
    # Nudge context
    # ------------------------------------------------------------------

    def set_nudge_context(self, phone_number: str, habit_id: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO nudge_context (phone_number, habit_id, sent_at) VALUES (%s, %s, NOW())
                ON CONFLICT (phone_number) DO UPDATE SET habit_id = EXCLUDED.habit_id, sent_at = NOW()
                """,
                (phone_number, habit_id),
            )
        self._commit()

    def get_nudge_context(self, phone_number: str) -> Optional[str]:
        expires_after = datetime.now() - timedelta(hours=24)
        with self._cursor() as cur:
            cur.execute(
                "SELECT habit_id FROM nudge_context WHERE phone_number = %s AND sent_at >= %s",
                (phone_number, expires_after),
            )
            row = cur.fetchone()
        return row["habit_id"] if row else None

    def clear_nudge_context(self, phone_number: str) -> None:
        with self._cursor() as cur:
            cur.execute("DELETE FROM nudge_context WHERE phone_number = %s", (phone_number,))
        self._commit()

    # ------------------------------------------------------------------
    # User settings
    # ------------------------------------------------------------------

    def get_user_setting(self, user_id: str, key: str) -> Optional[str]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT setting_value FROM user_settings WHERE user_id = %s AND setting_key = %s",
                (user_id, key),
            )
            row = cur.fetchone()
        return row["setting_value"] if row else None

    def set_user_setting(self, user_id: str, key: str, value: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_settings (user_id, setting_key, setting_value)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id, setting_key) DO UPDATE SET
                    setting_value = EXCLUDED.setting_value,
                    updated_at    = NOW()
                """,
                (user_id, key, value),
            )
        self._commit()

    def delete_user_setting(self, user_id: str, key: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM user_settings WHERE user_id = %s AND setting_key = %s", (user_id, key)
            )
        self._commit()

    def log_security_event(self, event_id: str, user_id: str, event_type: str, severity: str, snippet: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO security_events
                    (event_id, user_id, event_type, severity, snippet)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (event_id, user_id, event_type, severity, snippet),
            )
        self._commit()

    # ------------------------------------------------------------------
    # Habits (delegated to HabitService but registry needs these stubs)
    # ------------------------------------------------------------------

    def initialize_schema(self) -> None:
        pass  # schema managed via Supabase migrations

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
        import json as _json
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO hitl_requests (id, user_id, session_id, action_type, action_payload)
                VALUES (%s, %s, %s, %s, %s::jsonb)
                """,
                (id, user_id, session_id, action_type, _json.dumps(action_payload)),
            )
        self._commit()
        return id

    def get_hitl_request(self, id: str) -> Optional[Dict[str, object]]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM hitl_requests WHERE id = %s", (id,))
            row = cur.fetchone()
        if row is None:
            return None
        data = dict(row)
        # Ensure expires_at is timezone-aware
        expires = data.get("expires_at")
        if expires is not None and expires.tzinfo is None:
            from datetime import timezone
            data["expires_at"] = expires.replace(tzinfo=timezone.utc)
        return data

    def resolve_hitl_request(self, id: str, status: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE hitl_requests SET status = %s, resolved_at = NOW() WHERE id = %s",
                (status, id),
            )
        self._commit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row) -> Optional[Dict[str, object]]:
        if row is None:
            return None
        data = dict(row)
        for key in ("metadata_json",):
            if key in data and isinstance(data[key], str):
                data[key] = json.loads(data[key])
        return data
