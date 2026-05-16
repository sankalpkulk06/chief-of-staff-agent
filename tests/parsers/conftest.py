from pathlib import Path

import pytest
from pypdf import PdfWriter


@pytest.fixture
def docs_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "docs"


@pytest.fixture
def blank_pdf_path(tmp_path: Path) -> Path:
    pdf_path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with pdf_path.open("wb") as file_obj:
        writer.write(file_obj)
    return pdf_path

