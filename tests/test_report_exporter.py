import csv
import io
from report_exporter import export_csv, export_pdf


SAMPLE_RESULTS = [
    {
        "bib_key": "smith2020",
        "title": "ML Study",
        "authors": ["John Smith"],
        "year": "2020",
        "journal": "J AI",
        "doi": "10.1234/test",
        "abstract": "Studies ML.",
        "pdf_url": "https://example.com/paper.pdf",
        "url": "https://doi.org/10.1234/test",
        "citation_count": 42,
        "sources": ["crossref", "semantic_scholar"],
        "status": "found_pdf",
        "error": None,
    }
]


def test_export_csv():
    output = export_csv(SAMPLE_RESULTS)
    reader = csv.DictReader(io.StringIO(output))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["title"] == "ML Study"
    assert rows[0]["status"] == "found_pdf"
    assert "bib_key" in reader.fieldnames


def test_export_pdf():
    pdf_bytes = export_pdf(SAMPLE_RESULTS)
    assert pdf_bytes[:4] == b"%PDF"
    assert len(pdf_bytes) > 100
