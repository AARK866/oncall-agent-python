from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.rag import load_enterprise_documents


def test_enterprise_loader_reads_supported_formats_and_standardizes_metadata(tmp_path) -> None:
    (tmp_path / "runbook.md").write_text("# Markdown Runbook\n\nPayment 5xx.", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("Order timeout notes.", encoding="utf-8")
    _write_pdf(tmp_path / "database.pdf", "Database pool recovery")
    _write_docx(tmp_path / "rollback.docx", "Deployment rollback guide")
    (tmp_path / "ignored.json").write_text("{}", encoding="utf-8")

    documents = load_enterprise_documents(
        tmp_path,
        access_scope="restricted",
        allowed_roles=["sre", "oncall"],
    )

    assert [document.doc_id for document in documents] == [
        "database.pdf",
        "notes.txt",
        "rollback.docx",
        "runbook.md",
    ]
    by_id = {document.doc_id: document for document in documents}
    assert "Database pool recovery" in by_id["database.pdf"].content
    assert "Deployment rollback guide" in by_id["rollback.docx"].content
    assert by_id["database.pdf"].metadata["page_count"] == 1
    assert by_id["runbook.md"].metadata["source_version"]
    assert by_id["runbook.md"].metadata["updated_at"]
    assert by_id["runbook.md"].metadata["access_scope"] == "restricted"
    assert by_id["runbook.md"].metadata["allowed_roles"] == ["oncall", "sre"]
    assert by_id["runbook.md"].metadata["parser"] == "llamaindex-simple-directory-reader"


def _write_pdf(path: Path, text: str) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    with path.open("wb") as output:
        writer.write(output)


def _write_docx(path: Path, text: str) -> None:
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)
