import io
from email.message import EmailMessage
from docx import Document
from proton_rag.extract import extract, MAX_RAW


def test_body_csv_docx_and_skipped_media():
    msg = EmailMessage()
    msg.set_content("Cobalt arrives Tuesday")
    msg.add_attachment(
        b"part,quantity\ncobalt,42\n", maintype="text", subtype="csv", filename="parts.csv"
    )
    doc = Document()
    doc.add_paragraph("Warehouse violet shelf 12")
    stream = io.BytesIO()
    doc.save(stream)
    msg.add_attachment(
        stream.getvalue(),
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="notes.docx",
    )
    msg.add_attachment(b"not an image", maintype="image", subtype="png", filename="picture.png")
    value = extract(msg.as_bytes())
    assert "Tuesday" in value["text"] and "cobalt,42" in value["text"]
    assert "violet shelf 12" in value["text"] and value["skipped"] == ["unsupported"]


def test_html_no_scripts_empty_and_oversize():
    value = extract(b"Content-Type: text/html\n\n<p>Safe text</p><script>bad()</script>")
    assert value["text"] == "Safe text"
    assert extract(b"")["text"] == ""
    assert extract(b"x" * (MAX_RAW + 1))["skipped"] == ["message_size"]


def test_malformed_pdf_does_not_prevent_body():
    msg = EmailMessage()
    msg.set_content("Keep this body")
    msg.add_attachment(b"bad pdf", maintype="application", subtype="pdf", filename="bad.pdf")
    result = extract(msg.as_bytes())
    assert "Keep this body" in result["text"]
    assert result["skipped"] == ["malformed_attachment"]


def test_valid_pdf_text_extracted():
    # Minimal deterministic PDF fixture: no downloaded/private documents.
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = b"BT /F1 12 Tf 20 200 Td (PDF cobalt receipt 42) Tj ET"
    objects.append(
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
    )
    pdf = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    start = len(pdf)
    pdf += b"xref\n0 6\n0000000000 65535 f \n"
    pdf += b"".join(f"{n:010d} 00000 n \n".encode() for n in offsets)
    pdf += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(start).encode() + b"\n%%EOF"
    msg = EmailMessage()
    msg.set_content("Receipt attached")
    msg.add_attachment(pdf, maintype="application", subtype="pdf", filename="receipt.pdf")
    result = extract(msg.as_bytes())
    assert "PDF cobalt receipt 42" in result["text"]
    assert not result["skipped"]


def test_text_limit_is_explicit_in_extraction_result():
    from proton_rag.config import Settings
    from proton_rag.extract import parse

    parsed = parse(b"Subject: long\n\n0123456789", Settings(max_text_chars=5))
    assert parsed["text_truncated"] is True
    assert parsed["text"].endswith("01234")
