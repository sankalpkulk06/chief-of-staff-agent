from app.parsers.txt_parser import TextParser


def test_txt_parser_returns_normalized_document(docs_dir):
    parser = TextParser()
    file_path = docs_dir / "sample.txt"

    first = parser.parse(file_path)
    second = parser.parse(file_path)

    assert first.source_path == file_path.resolve()
    assert first.filename == "sample.txt"
    assert first.extension == ".txt"
    assert first.parser_name == "txt"
    assert first.content == "Line one.\nLine two with trailing spaces.\n\nLine four."
    assert first.char_count == len(first.content)
    assert first.checksum_sha256 == second.checksum_sha256
    assert first.metadata == {}

