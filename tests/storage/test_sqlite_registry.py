from pathlib import Path
from datetime import datetime, timedelta

from app.schemas.chunk import DocumentChunk
from app.schemas.document import ParsedDocument
from app.storage.sqlite_registry import SQLiteRegistry


def _build_document(source_path: Path, content: str = "Hello world") -> ParsedDocument:
    return ParsedDocument(
        source_path=source_path.resolve(),
        filename=source_path.name,
        extension=source_path.suffix.lower(),
        checksum_sha256="a" * 64,
        parser_name="txt",
        content=content,
        char_count=len(content),
        metadata={"tag": "study"},
    )


def _build_chunk(source_path: Path, document_id: str, text: str = "Hello") -> DocumentChunk:
    return DocumentChunk(
        chunk_id="chk_123",
        document_id=document_id,
        document_checksum_sha256="a" * 64,
        source_path=source_path.resolve(),
        file_name=source_path.name,
        chunk_index=0,
        text=text,
        token_count=1,
        char_start=0,
        char_end=len(text),
        metadata={"section": "intro"},
    )


def test_sqlite_registry_initializes_schema(tmp_path):
    db_path = tmp_path / "registry.db"
    registry = SQLiteRegistry(db_path=db_path)
    try:
        document_tables = registry._connection.execute(  # noqa: SLF001
            """
            SELECT name FROM sqlite_master
            WHERE type='table'
              AND name IN (
                'documents',
                'chunks',
                'todos',
                'nudge_context',
                'whatsapp_usage_daily',
                'whatsapp_usage_alerts'
              )
            """
        ).fetchall()
        table_names = sorted([row["name"] for row in document_tables])
        assert table_names == [
            "chunks",
            "documents",
            "nudge_context",
            "todos",
            "whatsapp_usage_alerts",
            "whatsapp_usage_daily",
        ]
        document_columns = {
            row["name"] for row in registry._connection.execute("PRAGMA table_info(documents)").fetchall()  # noqa: SLF001
        }
        assert {"source_type", "source_url", "ingested_at"} <= document_columns
    finally:
        registry.close()


def test_todo_queries_exclude_completed_and_notified(tmp_path):
    registry = SQLiteRegistry(db_path=tmp_path / "registry.db")
    try:
        due = datetime.now() + timedelta(minutes=30)
        overdue = datetime.now() - timedelta(minutes=5)
        future = datetime.now() + timedelta(hours=2)

        due_todo = registry.create_todo("Pay bill", due_at=due)
        notified = registry.create_todo("Already nudged", due_at=overdue)
        completed = registry.create_todo("Already done", due_at=overdue)
        future_todo = registry.create_todo("Later", due_at=future)
        registry.create_todo("No due date")

        registry.mark_todo_notified(notified["id"])
        registry.mark_todo_completed(completed["id"])

        due_soon = registry.get_todos_due_soon(minutes_ahead=60)
        pending = registry.get_pending_todos()

        assert [todo["id"] for todo in due_soon] == [due_todo["id"]]
        assert {todo["id"] for todo in pending} == {due_todo["id"], future_todo["id"]}
    finally:
        registry.close()


def test_nudge_context_persists_and_expires(tmp_path):
    db_path = tmp_path / "registry.db"
    registry = SQLiteRegistry(db_path=db_path)
    try:
        habit = registry._connection.execute(  # noqa: SLF001
            "INSERT INTO habits (id, name) VALUES (?, ?) RETURNING id",
            ("habit-1", "gym"),
        ).fetchone()
        registry.set_nudge_context("whatsapp:+1", habit["id"])
        assert registry.get_nudge_context("whatsapp:+1") == "habit-1"
    finally:
        registry.close()

    registry = SQLiteRegistry(db_path=db_path)
    try:
        assert registry.get_nudge_context("whatsapp:+1") == "habit-1"
        registry._connection.execute(  # noqa: SLF001
            "UPDATE nudge_context SET sent_at = ? WHERE phone_number = ?",
            ((datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"), "whatsapp:+1"),
        )
        registry._connection.commit()  # noqa: SLF001
        assert registry.get_nudge_context("whatsapp:+1") is None
    finally:
        registry.close()


def test_whatsapp_usage_tracks_count_and_alert_flags(tmp_path):
    registry = SQLiteRegistry(db_path=tmp_path / "registry.db")
    try:
        assert registry.get_whatsapp_usage_today(daily_limit=50)["sent_count"] == 0

        assert registry.record_whatsapp_message_sent() == 1
        assert registry.record_whatsapp_message_sent() == 2

        usage = registry.get_whatsapp_usage_today(daily_limit=50)
        assert usage["sent_count"] == 2
        assert usage["remaining"] == 48

        assert registry.has_whatsapp_usage_alert_sent(25) is False
        registry.mark_whatsapp_usage_alert_sent(25)
        assert registry.has_whatsapp_usage_alert_sent(25) is True
    finally:
        registry.close()


def test_chat_usage_counts_cli_and_whatsapp_user_turns(tmp_path):
    registry = SQLiteRegistry(db_path=tmp_path / "registry.db")
    try:
        cli_session = registry.get_or_create_named_session("cli:default")
        whatsapp_session = registry.get_or_create_whatsapp_session("whatsapp:+1")
        other_session = "other-session"
        registry.create_session(other_session)

        registry.append_turn(cli_session, "cli-user-1", "user", "hello", 0)
        registry.append_turn(cli_session, "cli-assistant-1", "assistant", "hi", 1)
        registry.append_turn(whatsapp_session, "wa-user-1", "user", "hello", 0)
        registry.append_turn(whatsapp_session, "wa-user-2", "user", "again", 1)
        registry.append_turn(other_session, "other-user-1", "user", "other", 0)

        usage = registry.get_chat_usage_today()

        assert usage["cli"] == 1
        assert usage["whatsapp"] == 2
        assert usage["other"] == 1
        assert usage["total"] == 4
    finally:
        registry.close()


def test_sqlite_registry_upsert_and_lookup_document_and_chunk(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("Hello world", encoding="utf-8")

    registry = SQLiteRegistry(db_path=tmp_path / "registry.db")
    try:
        document_id = "doc_abc"
        document = _build_document(file_path)
        chunk = _build_chunk(file_path, document_id=document_id)

        registry.upsert_document(document_id=document_id, document=document)
        registry.upsert_chunk(chunk)

        stored_doc = registry.get_document(document_id)
        stored_chunk = registry.get_chunk(chunk.chunk_id)
        chunks_for_doc = registry.get_chunks_for_document(document_id)

        assert stored_doc is not None
        assert stored_doc["document_id"] == document_id
        assert stored_doc["file_name"] == "sample.txt"
        assert stored_doc["source_type"] == "local"
        assert stored_doc["ingested_at"] is not None
        assert stored_doc["metadata_json"]["tag"] == "study"

        assert stored_chunk is not None
        assert stored_chunk["chunk_id"] == chunk.chunk_id
        assert stored_chunk["document_id"] == document_id
        assert stored_chunk["metadata_json"]["section"] == "intro"

        assert len(chunks_for_doc) == 1
        assert chunks_for_doc[0]["chunk_id"] == chunk.chunk_id
    finally:
        registry.close()


def test_sqlite_registry_repeated_upsert_updates_values(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("Hello world", encoding="utf-8")

    registry = SQLiteRegistry(db_path=tmp_path / "registry.db")
    try:
        document_id = "doc_abc"
        base_document = _build_document(file_path, content="Hello world")
        updated_document = _build_document(file_path, content="Hello updated world")
        updated_document.metadata = {"tag": "updated"}

        registry.upsert_document(document_id=document_id, document=base_document)
        registry.upsert_document(document_id=document_id, document=updated_document)

        base_chunk = _build_chunk(file_path, document_id=document_id, text="Hello")
        updated_chunk = _build_chunk(file_path, document_id=document_id, text="Hello updated")
        registry.upsert_chunk(base_chunk)
        registry.upsert_chunk(updated_chunk)

        stored_doc = registry.get_document(document_id)
        stored_chunk = registry.get_chunk(base_chunk.chunk_id)

        assert stored_doc is not None
        assert stored_doc["content_length"] == len("Hello updated world")
        assert stored_doc["metadata_json"]["tag"] == "updated"

        assert stored_chunk is not None
        assert stored_chunk["text"] == "Hello updated"
        assert stored_chunk["char_end"] == len("Hello updated")
    finally:
        registry.close()
