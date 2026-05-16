from app.parsers.pdf_parser import PdfParser


def test_pdf_parser_handles_blank_pdf_gracefully(blank_pdf_path):
    parser = PdfParser()

    parsed = parser.parse(blank_pdf_path)

    assert parsed.source_path == blank_pdf_path.resolve()
    assert parsed.extension == ".pdf"
    assert parsed.parser_name == "pdf"
    assert parsed.content == ""
    assert parsed.char_count == 0
    assert parsed.metadata["page_count"] == 1
    assert parsed.metadata["extracted_pages"] == 0

