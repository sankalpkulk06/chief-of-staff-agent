from app.parsers.md_parser import MarkdownParser


def test_markdown_parser_returns_normalized_document(docs_dir):
    parser = MarkdownParser()
    file_path = docs_dir / "sample.md"

    parsed = parser.parse(file_path)

    assert parsed.source_path == file_path.resolve()
    assert parsed.filename == "sample.md"
    assert parsed.extension == ".md"
    assert parsed.parser_name == "md"
    assert parsed.content.startswith("# Study Notes")
    assert "`RAG` basics." in parsed.content
    assert parsed.char_count == len(parsed.content)
    assert parsed.metadata == {}

