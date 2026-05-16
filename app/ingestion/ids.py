import hashlib
from pathlib import Path


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_document_id(source_path: Path, checksum_sha256: str) -> str:
    source = source_path.resolve().as_posix()
    stable_input = f"{source}|{checksum_sha256}"
    return f"doc_{_sha256_hex(stable_input)[:24]}"


def build_chunk_id(document_id: str, chunk_index: int, char_start: int, char_end: int, text: str) -> str:
    stable_input = f"{document_id}|{chunk_index}|{char_start}|{char_end}|{text}"
    return f"chk_{_sha256_hex(stable_input)[:24]}"

