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
        entry_type = entry.entry_type.lower() if hasattr(entry, 'entry_type') else ""
        fields = {f.key: f.value for f in entry.fields}

        title = _clean_latex(fields.get("title", "").strip())
        doi = fields.get("doi", "").strip()
        authors = _clean_latex(fields.get("author", ""))
        year = fields.get("year", "").strip()
        journal = _clean_latex(fields.get("journal", "") or fields.get("booktitle", ""))
        url = fields.get("url", "").strip()

        # Extract URL from howpublished or note if url field is empty
        if not url:
            for fallback_field in ("howpublished", "note"):
                val = fields.get(fallback_field, "")
                m = re.search(r'\\url\{([^}]+)\}', val)
                if m:
                    url = m.group(1).strip()
                    break
                m = re.search(r'(https?://[^\s,}]+)', val)
                if m:
                    url = m.group(1).strip()
                    break

        # Extract arXiv ID from eprint field, URL, DOI, or any other field containing
        # an "arXiv:NNNN.NNNNN" marker (common in journal/note/howpublished when authors
        # cite preprints — e.g. `journal = {arXiv preprint arXiv:2111.09395}`).
        arxiv_id = None
        eprint = fields.get("eprint", "").strip()
        if eprint and fields.get("archiveprefix", "").strip().lower() == "arxiv":
            arxiv_id = eprint
        elif eprint and re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", eprint):
            arxiv_id = eprint
        if not arxiv_id and url:
            m = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)", url)
            if m:
                arxiv_id = m.group(1)
        if not arxiv_id and doi:
            m = re.match(r"10\.48550/arXiv\.(.+)", doi, re.IGNORECASE)
            if m:
                arxiv_id = m.group(1)
        if not arxiv_id:
            # Scan common free-text fields for "arXiv:NNNN.NNNNN"
            for fname in ("journal", "booktitle", "note", "howpublished", "series"):
                fval = fields.get(fname, "")
                if not fval:
                    continue
                m = re.search(r"arXiv\s*:\s*(\d{4}\.\d{4,5}(?:v\d+)?)", fval, re.IGNORECASE)
                if m:
                    arxiv_id = m.group(1)
                    break

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

        # Build raw BibTeX string for display
        raw_bib = f"@{entry_type}{{{bib_key},\n"
        for f in entry.fields:
            raw_bib += f"  {f.key} = {{{f.value}}},\n"
        raw_bib += "}"

        results.append({
            "bib_key": bib_key,
            "entry_type": entry_type,
            "title": title or None,
            "authors": authors,
            "year": year or None,
            "journal": journal or None,
            "doi": doi or None,
            "url": url or None,
            "arxiv_id": arxiv_id,
            "status": status,
            "all_fields": {f.key: f.value for f in entry.fields},
            "raw_bib": raw_bib,
        })

    return results
