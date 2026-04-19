"""Tests for tex_parser.parse_tex_citations.

Pins:
- Plain \\cite, \\citep, \\citet, \\parencite, \\textcite, \\autocite captured
- Multi-key cites split into one entry per key
- Citations inside LaTeX comments are SKIPPED:
    %\\cite{x}                        — line-leading comment
    text % comment with \\cite{x}     — trailing comment
    \\\\%\\cite{x}                    — escaped backslash before %, IS a comment
- Escaped percent (`\\%`) does NOT start a comment, so a cite after `\\%` IS captured
- Line numbers + positions stay accurate when commented cites are skipped
"""

from tex_parser import parse_tex_citations


class TestCommentedCitations:
    """Regression: a commented-out citation like %\\cite{chiang2025llm} must
    NOT appear in the citation list — it's not part of the document."""

    def test_line_leading_comment_skipped(self):
        tex = "Real cite \\cite{good} here.\n%\\cite{commented_out}\nMore text."
        cites = parse_tex_citations(tex)
        keys = [c["bib_key"] for c in cites]
        assert keys == ["good"]

    def test_trailing_comment_skipped(self):
        tex = "Real cite \\cite{good} here. % old: \\cite{commented_out}\n"
        cites = parse_tex_citations(tex)
        keys = [c["bib_key"] for c in cites]
        assert keys == ["good"]

    def test_indented_comment_skipped(self):
        tex = "Some text.\n    %  \\cite{commented_out}\nReal cite \\cite{good}.\n"
        cites = parse_tex_citations(tex)
        keys = [c["bib_key"] for c in cites]
        assert keys == ["good"]

    def test_multi_key_in_comment_skipped(self):
        tex = "%\\cite{a, b, c}\n\\cite{d}\n"
        cites = parse_tex_citations(tex)
        assert [c["bib_key"] for c in cites] == ["d"]

    def test_escaped_percent_does_not_start_comment(self):
        # In LaTeX, \% is a literal percent. The cite that follows is real.
        tex = "Discount of 50\\% and \\cite{good} applies.\n"
        cites = parse_tex_citations(tex)
        assert [c["bib_key"] for c in cites] == ["good"]

    def test_double_backslash_then_percent_is_a_comment(self):
        # \\ is an escaped backslash, then % is a real comment marker.
        tex = "Hard line break \\\\% \\cite{commented_out}\nNext line."
        cites = parse_tex_citations(tex)
        assert cites == []

    def test_only_commented_cites_returns_empty(self):
        tex = "%\\cite{a}\n%\\cite{b}\n%\\citep{c}\n"
        cites = parse_tex_citations(tex)
        assert cites == []

    def test_user_regression_chiang2025llm(self):
        """The exact example from the bug report."""
        tex = (
            "Some text introducing the topic.\n"
            "%\\cite{chiang2025llm}\n"
            "More text \\cite{real_ref} explaining things.\n"
        )
        cites = parse_tex_citations(tex)
        assert [c["bib_key"] for c in cites] == ["real_ref"]


class TestCommentedCitationsPositionFidelity:
    """Skipping commented cites must not corrupt line numbers / positions
    for the cites we DO keep."""

    def test_line_numbers_unchanged_after_skip(self):
        tex = (
            "line 1\n"
            "%\\cite{commented_out}\n"   # line 2
            "line 3 \\cite{good} here\n"   # line 3
        )
        cites = parse_tex_citations(tex)
        assert len(cites) == 1
        assert cites[0]["bib_key"] == "good"
        assert cites[0]["line"] == 3

    def test_position_points_at_real_cite(self):
        tex = "%\\cite{commented}\n\\cite{good}\n"
        cites = parse_tex_citations(tex)
        assert len(cites) == 1
        # The captured pos must point at the start of the REAL \cite{good}
        assert tex[cites[0]["position"]:].startswith("\\cite{good}")


class TestNonCommentedCitations:
    """Sanity checks — the existing happy path still works."""

    def test_plain_cite_captured(self):
        cites = parse_tex_citations("See \\cite{smith2020} for details.")
        assert [c["bib_key"] for c in cites] == ["smith2020"]

    def test_multi_key_split(self):
        cites = parse_tex_citations("\\cite{a, b, c}")
        assert [c["bib_key"] for c in cites] == ["a", "b", "c"]

    def test_variant_cite_commands(self):
        tex = (
            "\\cite{a} \\citep{b} \\citet{c} "
            "\\parencite{d} \\textcite{e} \\autocite{f} \\fullcite{g}"
        )
        cites = parse_tex_citations(tex)
        assert sorted(c["bib_key"] for c in cites) == list("abcdefg")

    def test_optional_arg_cite(self):
        cites = parse_tex_citations("\\cite[p.~42]{smith2020}")
        assert [c["bib_key"] for c in cites] == ["smith2020"]
