import csv
import io
from fpdf import FPDF

CSV_FIELDS = [
    "bib_key", "title", "authors", "year", "journal", "doi",
    "abstract", "pdf_url", "url", "citation_count", "sources", "status"
]


def export_csv(results):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for r in results:
        row = dict(r)
        row["authors"] = "; ".join(row.get("authors", []))
        row["sources"] = ", ".join(row.get("sources", []))
        writer.writerow(row)
    return output.getvalue()


def export_pdf(results):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "References Checker Report", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(5)

    # Summary
    total = len(results)
    found_pdf = sum(1 for r in results if r["status"] == "found_pdf")
    found_abstract = sum(1 for r in results if r["status"] == "found_abstract")
    not_found = total - found_pdf - found_abstract
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Total: {total} | PDF found: {found_pdf} | Abstract only: {found_abstract} | Not found: {not_found}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # References
    for i, r in enumerate(results, 1):
        pdf.set_font("Helvetica", "B", 11)
        title = r.get("title") or "(No title)"
        pdf.multi_cell(0, 6, f"{i}. {title}")
        pdf.set_font("Helvetica", "", 9)
        authors = "; ".join(r.get("authors", []))
        if authors:
            pdf.cell(0, 5, f"Authors: {authors}", new_x="LMARGIN", new_y="NEXT")
        if r.get("year"):
            pdf.cell(0, 5, f"Year: {r['year']}", new_x="LMARGIN", new_y="NEXT")
        if r.get("journal"):
            pdf.cell(0, 5, f"Journal: {r['journal']}", new_x="LMARGIN", new_y="NEXT")
        if r.get("doi"):
            pdf.cell(0, 5, f"DOI: {r['doi']}", new_x="LMARGIN", new_y="NEXT")
        status_label = {"found_pdf": "Full Paper", "found_abstract": "Abstract Only",
                        "not_found": "Not Found", "insufficient_data": "Insufficient Data",
                        "parse_error": "Parse Error"}.get(r["status"], r["status"])
        pdf.cell(0, 5, f"Status: {status_label}", new_x="LMARGIN", new_y="NEXT")
        if r.get("pdf_url"):
            pdf.cell(0, 5, f"PDF: {r['pdf_url']}", new_x="LMARGIN", new_y="NEXT")
        if r.get("abstract"):
            pdf.set_font("Helvetica", "I", 8)
            pdf.multi_cell(0, 4, f"Abstract: {r['abstract'][:500]}")
        pdf.ln(4)

    return pdf.output()
