import re
import bibtexparser


def _clean_latex(text):
    """Remove LaTeX artifacts from text: braces, commands, etc."""
    if not text:
        return text
    # Remove LaTeX commands like \"{o}, \'{e}, \c{c}, \H{o}, etc. → keep the letter
    text = re.sub(r'\\["\'^`~Hcvudtb]\{(\w)\}', r'\1', text)
    text = re.sub(r'\\["\'^`~Hcvudtb](\w)', r'\1', text)
    # Remove other LaTeX commands like \textbf{...} → keep content
    text = re.sub(r'\\[a-zA-Z]+\{([^}]*)\}', r'\1', text)
    # Remove remaining braces
    text = text.replace('{', '').replace('}', '')
    # Remove backslashes
    text = text.replace('\\', '')
    # Clean up multiple spaces
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def parse_bib_file(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        bib_string = f.read()
    return parse_bib_string(bib_string)


def parse_bib_string(bib_string):
    if not bib_string.strip():
        return []

    library = bibtexparser.parse_string(bib_string)
    seen_dois = set()
    seen_titles = set()
    results = []

    # Include failed/malformed entries as parse errors
    for block in library.failed_blocks:
        results.append({
            "bib_key": f"parse_error_{len(results)}",
            "title": None,
            "authors": "",
            "year": None,
            "journal": None,
            "doi": None,
            "url": None,
            "status": "parse_error",
            "raw": str(block.raw),
        })

    for entry in library.entries:
        bib_key = entry.key
        fields = {f.key: f.value for f in entry.fields}

        title = _clean_latex(fields.get("title", "").strip())
        doi = fields.get("doi", "").strip()
        authors = _clean_latex(fields.get("author", ""))
        year = fields.get("year", "").strip()
        journal = _clean_latex(fields.get("journal", "") or fields.get("booktitle", ""))
        url = fields.get("url", "").strip()

        # Deduplicate by DOI
        if doi:
            if doi in seen_dois:
                continue
            seen_dois.add(doi)
        elif title:
            norm_title = title.lower().strip()
            if norm_title in seen_titles:
                continue
            seen_titles.add(norm_title)

        # Determine status for entries with no useful data
        status = None
        if not title and not doi:
            status = "insufficient_data"

        results.append({
            "bib_key": bib_key,
            "title": title or None,
            "authors": authors,
            "year": year or None,
            "journal": journal or None,
            "doi": doi or None,
            "url": url or None,
            "status": status,
        })

    return results
