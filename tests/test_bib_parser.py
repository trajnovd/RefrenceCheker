from bib_parser import parse_bib_file, parse_bib_string

def test_parse_basic_entries():
    results = parse_bib_file("tests/fixtures/sample.bib")
    titles = [r["title"] for r in results if r.get("title")]
    assert "A Study on Machine Learning" in titles
    assert "Deep Learning in Practice" in titles

def test_extracts_all_fields():
    results = parse_bib_file("tests/fixtures/sample.bib")
    smith = next(r for r in results if r["bib_key"] == "smith2020")
    assert smith["doi"] == "10.1234/example.2020"
    assert smith["year"] == "2020"
    assert "Smith" in smith["authors"]

def test_deduplicates_by_doi():
    results = parse_bib_file("tests/fixtures/sample.bib")
    doi_entries = [r for r in results if r.get("doi") == "10.1234/example.2020"]
    assert len(doi_entries) == 1

def test_handles_entry_with_no_title_no_doi():
    results = parse_bib_file("tests/fixtures/sample.bib")
    noinfo = next((r for r in results if r["bib_key"] == "noinfo"), None)
    assert noinfo is not None
    assert noinfo["status"] == "insufficient_data"

def test_handles_unicode():
    results = parse_bib_file("tests/fixtures/sample.bib")
    uni = next(r for r in results if r["bib_key"] == "unicode2021")
    assert uni["doi"] == "10.5678/unicode.2021"
    assert uni["title"] is not None

def test_extracts_arxiv_id_from_eprint_field():
    bib = "@article{x, title={T}, eprint={2111.09395}, archiveprefix={arXiv}}"
    refs = parse_bib_string(bib)
    assert refs[0]["arxiv_id"] == "2111.09395"


def test_extracts_arxiv_id_from_journal_field():
    """Regression: bib entries like @article{...journal={arXiv preprint arXiv:2111.09395}...}
    must yield arxiv_id so process_reference's Step 0 kicks in — otherwise lookup
    falls through to SSRN / publisher sites that often bot-block."""
    bib = """@article{liu2022finrl,
      author = {Xiao-Yang Liu and others},
      title = {FinRL: A Deep RL Library for Automated Stock Trading},
      journal = {arXiv preprint arXiv:2111.09395},
      year = {2022},
    }"""
    refs = parse_bib_string(bib)
    assert refs[0]["arxiv_id"] == "2111.09395"


def test_extracts_arxiv_id_from_note_and_howpublished():
    for field_name in ("note", "howpublished", "booktitle"):
        bib = '@misc{x, title={T}, ' + field_name + '={See arXiv:1706.03762 for details}}'
        refs = parse_bib_string(bib)
        assert refs[0]["arxiv_id"] == "1706.03762", f"missed arxiv_id in {field_name}"


def test_empty_file_returns_empty():
    import tempfile, os
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".bib", delete=False)
    f.write("")
    f.close()
    results = parse_bib_file(f.name)
    os.unlink(f.name)
    assert results == []
