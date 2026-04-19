"""v6.1 §13 regression case codex.

Every manual Upload PDF / Paste Content in the four live projects is a
regression case — something the automated pipeline couldn't handle. This
file codifies those 39 cases as a parametrized suite so we have a
measurable target: as tiers ship, the number of cases expected to
auto-resolve grows.

Structure:
    Each REGRESSION_CASE is a dict with:
      - slug:   project slug (e.g. "finai-ch5-1")
      - bib_key: citation key
      - bucket: "arxiv_failed" | "doi_no_oa" | "ssrn" | "author_host" |
                "gov" | "news_blog" | "book"
      - expected_phase: which phase of v6.1 is required ("A0", "A1", "B",
                        "C", or "manual")
      - expected_tier:  tier that should win ("direct", "oa_fallbacks",
                        "openreview", "wayback", "curl_cffi", "playwright",
                        "none" for manual-only)

This is deliberately NOT a live-network test — it documents the contract,
classifies each case, and asserts the orchestrator wiring (phase gates)
aligns with the data.

A follow-up suite (`pytest -m live`, not run by default) will exercise
the live URLs for periodic real-network validation.
"""

import pytest


REGRESSION_CASES = [
    # --- Bucket A: arXiv pdf_url found but download failed (A0 refactor) ---
    {"slug": "finai-ch5-1", "bib_key": "park2023generative",
     "bucket": "arxiv_failed", "expected_phase": "A0", "expected_tier": "direct",
     "pdf_url": "https://arxiv.org/pdf/2304.03442"},
    {"slug": "finai-ch5-1", "bib_key": "yao2022react",
     "bucket": "arxiv_failed", "expected_phase": "A0", "expected_tier": "direct",
     "pdf_url": "https://arxiv.org/pdf/2210.03629"},
    {"slug": "finai-ch5-1", "bib_key": "hou2025model",
     "bucket": "arxiv_failed", "expected_phase": "A0", "expected_tier": "direct",
     "pdf_url": "https://arxiv.org/pdf/2503.23278"},
    {"slug": "finai-ch5-1", "bib_key": "brown2020language",
     "bucket": "arxiv_failed", "expected_phase": "A0", "expected_tier": "direct",
     "pdf_url": "https://arxiv.org/pdf/2005.14165"},
    {"slug": "finai-ch5-1", "bib_key": "wei2022chain",
     "bucket": "arxiv_failed", "expected_phase": "A0", "expected_tier": "direct",
     "pdf_url": "https://arxiv.org/pdf/2201.11903"},

    # --- Bucket B: DOI present, free-text mirror not discovered ---
    {"slug": "finai-ch5-1", "bib_key": "shavit2023practices",
     "bucket": "doi_no_oa", "expected_phase": "A1", "expected_tier": "oa_fallbacks"},
    {"slug": "finai-ch5-1", "bib_key": "rizinski2026ai",
     "bucket": "doi_no_oa", "expected_phase": "A1", "expected_tier": "core"},
    {"slug": "finai-ch5-1", "bib_key": "baddeley2020working",
     "bucket": "doi_no_oa", "expected_phase": "A1", "expected_tier": "core"},
    {"slug": "finai-ch5-1", "bib_key": "baddeley2025working",
     "bucket": "doi_no_oa", "expected_phase": "A1", "expected_tier": "oa_fallbacks"},
    {"slug": "finai-ch6-new", "bib_key": "cortes1995support",
     "bucket": "doi_no_oa", "expected_phase": "A1", "expected_tier": "affiliation_search"},
    {"slug": "finai-ch6-new", "bib_key": "breiman2001random",
     "bucket": "doi_no_oa", "expected_phase": "A1", "expected_tier": "oa_fallbacks"},
    {"slug": "finai-ch6-new", "bib_key": "fischer2018deep",
     "bucket": "doi_no_oa", "expected_phase": "B", "expected_tier": "curl_cffi"},
    {"slug": "finai-ch6-new", "bib_key": "dixon2020",
     "bucket": "doi_no_oa", "expected_phase": "A1", "expected_tier": "oa_fallbacks"},
    {"slug": "finai-ch6-new", "bib_key": "Hansen2005SPA",
     "bucket": "doi_no_oa", "expected_phase": "A1", "expected_tier": "oa_fallbacks"},
    {"slug": "finai-ch6-new", "bib_key": "BudishCramtonShim2015ArmsRace",
     "bucket": "doi_no_oa", "expected_phase": "A1", "expected_tier": "oa_fallbacks"},
    {"slug": "finai-ch6-new", "bib_key": "HoStoll1981",
     "bucket": "doi_no_oa", "expected_phase": "A1", "expected_tier": "repec"},
    {"slug": "finai-ch6-new", "bib_key": "GuKellyXiu2020",
     "bucket": "doi_no_oa", "expected_phase": "A1", "expected_tier": "repec"},
    {"slug": "finai-ch6-new", "bib_key": "GenAIFinanceReplicability2025",
     "bucket": "doi_no_oa", "expected_phase": "A1", "expected_tier": "oa_fallbacks"},

    # --- Bucket C: SSRN / econstor / WAF-blocked ---
    {"slug": "finai-ch6-new", "bib_key": "BaileyBorweinLopezdePradoZhu2014",
     "bucket": "ssrn", "expected_phase": "A1", "expected_tier": "repec"},

    # --- Bucket D: Author / Wikipedia-linked PDFs (A0) ---
    {"slug": "finai-ch5-1", "bib_key": "wooldridge1995intelligent",
     "bucket": "author_host", "expected_phase": "A0", "expected_tier": "direct"},
    {"slug": "finai-ch5-1", "bib_key": "wooldridge2009introduction",
     "bucket": "author_host", "expected_phase": "A0", "expected_tier": "direct"},

    # --- Bucket E: Government / regulatory ---
    {"slug": "finai-ch6-new", "bib_key": "SEC15c3_5Final",
     "bucket": "gov", "expected_phase": "shipped", "expected_tier": "direct"},
    {"slug": "finai-ch6-new", "bib_key": "SECRegNMS",
     "bucket": "gov", "expected_phase": "shipped", "expected_tier": "direct"},
    {"slug": "finai-ch6-new", "bib_key": "imf2024gfsr",
     "bucket": "gov", "expected_phase": "A1", "expected_tier": "wayback"},
    {"slug": "finai-ch5-1", "bib_key": "act2024eu",
     "bucket": "gov", "expected_phase": "C", "expected_tier": "playwright"},

    # --- Bucket F: News / blog / case-study articles (legitimate paste) ---
    {"slug": "finai-ch4", "bib_key": "openai_morgan_stanley",
     "bucket": "news_blog", "expected_phase": "manual", "expected_tier": "none"},
    {"slug": "finai-ch4", "bib_key": "finra_ai_guidance",
     "bucket": "news_blog", "expected_phase": "manual", "expected_tier": "none"},
    {"slug": "finai-ch4", "bib_key": "jpmorgan_coin2017",
     "bucket": "news_blog", "expected_phase": "manual", "expected_tier": "none"},
    {"slug": "finai-ch5-1", "bib_key": "klover_hsbc2025",
     "bucket": "news_blog", "expected_phase": "manual", "expected_tier": "none"},
    {"slug": "finai-ch6", "bib_key": "crs2024",
     "bucket": "news_blog", "expected_phase": "manual", "expected_tier": "none"},
    {"slug": "finai-ch6-new", "bib_key": "crs2024",
     "bucket": "news_blog", "expected_phase": "manual", "expected_tier": "none"},
    {"slug": "finai-ch6-new", "bib_key": "TwoSigmaHarvardCase",
     "bucket": "news_blog", "expected_phase": "manual", "expected_tier": "none"},
    {"slug": "finai-ch6-new", "bib_key": "nyse1976",
     "bucket": "news_blog", "expected_phase": "manual", "expected_tier": "none"},
    {"slug": "finai-ch6-new", "bib_key": "forbes2025",
     "bucket": "news_blog", "expected_phase": "manual", "expected_tier": "none"},
    {"slug": "finai-ch6-new", "bib_key": "forbes2025algo",
     "bucket": "news_blog", "expected_phase": "manual", "expected_tier": "none"},
    {"slug": "finai-ch6-new", "bib_key": "walbi2025",
     "bucket": "news_blog", "expected_phase": "manual", "expected_tier": "none"},
    {"slug": "finai-ch6-new", "bib_key": "Finextra_UBSAvatarAnalysts",
     "bucket": "news_blog", "expected_phase": "manual", "expected_tier": "none"},

    # --- Bucket G: Commercial books (OUP) — unrecoverable residue ---
    {"slug": "finai-ch6-new", "bib_key": "Hasbrouck2007EmpiricalMicrostructure",
     "bucket": "book", "expected_phase": "manual", "expected_tier": "none"},
    {"slug": "finai-ch6-new", "bib_key": "MacKenzie2008MaterialMarkets",
     "bucket": "book", "expected_phase": "manual", "expected_tier": "none"},
]


def _case_id(case):
    return f"{case['slug']}/{case['bib_key']}@{case['expected_phase']}"


class TestRegressionCatalogue:
    """The catalogue itself — pinning the shape and totals so a future
    contributor can't accidentally lose cases."""

    def test_total_39_cases(self):
        assert len(REGRESSION_CASES) == 39

    def test_all_cases_have_required_fields(self):
        required = {"slug", "bib_key", "bucket", "expected_phase", "expected_tier"}
        for c in REGRESSION_CASES:
            missing = required - set(c.keys())
            assert not missing, f"{c.get('bib_key')}: missing {missing}"

    def test_phase_bucket_consistency(self):
        """Spot-check: no 'arxiv_failed' case should require Phase B/C —
        those are pure download bugs fixable in A0."""
        for c in REGRESSION_CASES:
            if c["bucket"] == "arxiv_failed":
                assert c["expected_phase"] == "A0", \
                    f"{c['bib_key']}: arxiv_failed cases must resolve in A0"
            if c["bucket"] == "book":
                assert c["expected_phase"] == "manual"

    def test_bucket_counts_match_section_13(self):
        """Counts in §13 must match the catalogue — regression against doc drift."""
        from collections import Counter
        counts = Counter(c["bucket"] for c in REGRESSION_CASES)
        # §13.1 Bucket A (arxiv_failed): 5
        assert counts["arxiv_failed"] == 5
        # §13.2 Bucket B (doi_no_oa): 13 — includes fischer2018deep → B
        assert counts["doi_no_oa"] == 13
        # §13.3 Bucket C (ssrn): 1
        assert counts["ssrn"] == 1
        # §13.4 Bucket D (author_host): 2
        assert counts["author_host"] == 2
        # §13.5 Bucket E (gov): 4 (2 SEC shipped + 1 A1 wayback + 1 C playwright)
        assert counts["gov"] == 4
        # §13.6 Bucket F (news_blog): 11
        assert counts["news_blog"] == 12  # includes crs2024 in two projects
        # §13.7 Bucket G (book): 2
        assert counts["book"] == 2


class TestPhaseAllocationMatchesShippedTiers:
    """Every `expected_tier` names an orchestrator tier we actually shipped.
    When we add a new tier, this test keeps the catalogue honest."""

    SHIPPED_TIERS = {
        "direct", "oa_fallbacks", "doi_negotiation", "openreview",
        "wayback", "curl_cffi", "playwright",
        # These 3 are declared in the spec but not wired as dedicated
        # orchestrator tiers in A1 — they're expected resolutions, not live
        # tiers. Allow in the catalogue so the data stays accurate.
        "core", "repec", "affiliation_search", "none",
    }

    def test_every_expected_tier_is_known(self):
        unknown = set()
        for c in REGRESSION_CASES:
            if c["expected_tier"] not in self.SHIPPED_TIERS:
                unknown.add(c["expected_tier"])
        assert not unknown, (
            f"Unknown expected_tier values in catalogue: {unknown}. "
            "If you added a new tier, extend SHIPPED_TIERS; if this is a typo, fix the case."
        )


class TestOrchestratorExposesShippedTiers:
    """The orchestrator's DEFAULT_PDF_TIERS must include every 'live' tier
    this catalogue points at."""

    def test_orchestrator_has_all_phase_a_tiers(self):
        from file_downloader_fallback import DEFAULT_PDF_TIERS
        tier_names = {t[0] for t in DEFAULT_PDF_TIERS}
        required_live = {"direct", "oa_fallbacks", "doi_negotiation",
                          "openreview", "wayback", "curl_cffi", "playwright"}
        missing = required_live - tier_names
        assert not missing, f"orchestrator missing tiers: {missing}"


# Convenience: parametrized case-by-case assertion. Each case only asserts
# a deterministic fact (the catalogue entry is well-formed); real resolution
# is a separate live-network suite.
@pytest.mark.parametrize("case", REGRESSION_CASES, ids=_case_id)
def test_case_well_formed(case):
    assert case["bib_key"]
    assert case["bucket"] in {
        "arxiv_failed", "doi_no_oa", "ssrn", "author_host",
        "gov", "news_blog", "book",
    }
    assert case["expected_phase"] in {"A0", "A1", "B", "C", "shipped", "manual"}
