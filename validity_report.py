"""Citation validity report generator (v1).

Renders a self-contained HTML report the author opens, scans top-to-bottom,
and uses to fix problematic citations in the .tex source. Plus a sibling
references.zip so the bundle can be downloaded and run on a laptop.

Spec: validity_report_v1.md
"""

import html
import logging
import os
import re
import shutil
import zipfile
from datetime import datetime

from config import PROJECTS_DIR, get_claim_check_settings, get_reference_match_settings
from file_downloader import _safe_filename
from reference_matcher import extract_first_pages, load_reference_md

logger = logging.getLogger(__name__)


# ============================================================
# Severity classification
# ============================================================
# One severity per citation OCCURRENCE. The top-bucket determines which
# section the block lands in (Problematic / Partial / Clean) and the order
# within the Problematic section. A block may surface signals from lower
# buckets too — e.g. an identity_not_matched block also shows the claim
# verdict if there is one.

# Ordered worst → best. Used as both bucket label and sort key.
SEVERITY_ORDER = [
    "parse_error",
    "missing_key",
    "broken_url",
    "identity_not_matched",
    "claim_not_supported",
    "no_md",
    "identity_unverifiable",
    "partial",
    "clean",
]
_SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITY_ORDER)}

# v6.1 §12.5 — per-tier explainer shown when a non-`direct` tier delivered
# the PDF. Rendered as a small info banner inside the Downloaded source block.
TIER_EXPLAINERS = {
    "wayback":         "historic Web Archive snapshot — may be outdated",
    "openreview":      "OpenReview accepted submission — may differ from camera-ready",
    "oa_fallbacks":    "alternate open-access mirror via Unpaywall/OpenAlex",
    "doi_negotiation": "direct PDF via DOI content negotiation",
    "core":            "institutional-repository copy via CORE aggregator",
    "hal":             "HAL open-access archive",
    "pmc":             "PubMed Central open-access copy",
    "nber":            "NBER working-paper PDF",
    "repec":           "RePEc mirror",
    "zenodo":          "Zenodo research archive",
    "osf":             "OSF Preprints",
    "curl_cffi":       "fetched with browser TLS impersonation (site bot-blocked default fetch)",
    "playwright":      "captured via headless browser (site needs JS rendering)",
    "manual_set_link": "manually set by you via Set Link",
    "manual_upload":   "manually uploaded by you",
    "manual_paste":    "manually pasted by you",
    "direct":          None,
}


# Display metadata per severity. Drives badges + suggested-fix text.
SEVERITY_META = {
    "parse_error":          {"emoji": "🚫", "label": "BIB PARSE ERROR",
                              "fix": "The bib entry could not be parsed (malformed BibTeX or no title/DOI). Fix the bib entry."},
    "missing_key":          {"emoji": "🚫", "label": "CITATION KEY NOT IN .BIB",
                              "fix": "The .tex cites a key that has no entry in the .bib file. Either add the bib entry or remove the citation."},
    "broken_url":           {"emoji": "🚫", "label": "BROKEN BIB URL",
                              "fix": "The URL in the bib entry is unreachable (HTTP error or network failure). Either correct the URL or use Set Link / Upload PDF / Paste Content to provide an alternate source."},
    "identity_not_matched": {"emoji": "❌", "label": "TITLE OR AUTHORS DO NOT MATCH",
                              "fix": "The downloaded text doesn't match the bib's title or authors. Likely causes: wrong arXiv ID, wrong DOI, or hallucinated reference. Verify the bib metadata against the actual paper, or remove the citation."},
    "claim_not_supported":  {"emoji": "❌", "label": "CLAIM NOT SUPPORTED",
                              "fix": "The downloaded reference does not back the claim. Either soften the claim, find a different reference, or remove the citation."},
    "no_md":                {"emoji": "❌", "label": "REFERENCE NOT FOUND",
                              "fix": "The reference could not be downloaded — no PDF, HTML, or abstract was retrievable. Use Set Link / Upload PDF / Paste Content to provide a source, or fix the bib entry if the citation is wrong."},
    "identity_unverifiable":{"emoji": "❓", "label": "IDENTITY UNVERIFIABLE",
                              "fix": "The LLM could not decide whether the downloaded text matches the bib. Glance at the source and confirm manually (Mark OK / Mark NOT)."},
    "partial":              {"emoji": "⚠️", "label": "CLAIM PARTIAL",
                              "fix": "The reference is tangentially related to the claim. Re-read and decide whether to strengthen the claim wording or remove the cite."},
    "clean":                {"emoji": "✓",  "label": "CLEAN", "fix": ""},
}


def _classify(citation, ref, claim_check):
    """Return the citation's severity bucket (worst-applicable signal)."""
    if ref is None:
        return "missing_key"
    status = ref.get("status")
    if status in ("parse_error", "insufficient_data"):
        return "parse_error"
    if status == "bib_url_unreachable":
        return "broken_url"

    rm = ref.get("ref_match") or {}
    if rm.get("verdict") in ("not_matched", "manual_not_matched"):
        return "identity_not_matched"

    if claim_check and claim_check.get("verdict") == "not_supported":
        return "claim_not_supported"

    files = ref.get("files") or {}
    if not files.get("md"):
        return "no_md"

    if rm.get("verdict") == "unverifiable":
        return "identity_unverifiable"

    if claim_check and claim_check.get("verdict") == "partial":
        return "partial"

    return "clean"


# ============================================================
# Per-citation context helpers
# ============================================================

def _paragraph_for_citation(tex_content, citation):
    """Return the paragraph around the cite, with a [CITE:bib_key] marker
    inserted at the cite's position so the author can locate it visually."""
    if not tex_content:
        return ""
    try:
        from tex_parser import extract_claim_context
        ctx = extract_claim_context(tex_content, citation,
                                    max_paragraph_chars=2400,
                                    max_sentence_chars=800)
    except Exception as e:
        logger.debug("extract_claim_context failed for %s: %s",
                     citation.get("bib_key"), e)
        # Fallback: window around the position
        pos = citation.get("position", 0)
        end = citation.get("end_position", pos)
        return tex_content[max(0, pos - 600):min(len(tex_content), end + 600)]
    return ctx.get("paragraph_clean") or ctx.get("paragraph") or ""


def _md_excerpt_for(project_dir, bib_key, max_chars=6000):
    """First ~6000 chars of the reference's .md body — same window the
    identity-check LLM sees. Empty string if the .md doesn't exist."""
    md = load_reference_md(project_dir, bib_key)
    if not md:
        return ""
    return extract_first_pages(md, max_chars)


# ============================================================
# File copy + zip
# ============================================================

def _copy_files_for_keys(project_dir, refs_dir, results, keys_with_files):
    """Copy every artifact in result.files for refs in keys_with_files into refs_dir.
    Returns list of (arcname, abs_src) for the zip step.
    """
    copied = []
    for r in results:
        if r.get("bib_key") not in keys_with_files:
            continue
        for fkey, fname in (r.get("files") or {}).items():
            if not fname:
                continue
            src = os.path.join(project_dir, fname)
            if os.path.isfile(src):
                dst = os.path.join(refs_dir, fname)
                try:
                    shutil.copy2(src, dst)
                    copied.append((f"references/{fname}", dst))
                except OSError as e:
                    logger.debug("Failed to copy %s -> %s: %s", src, dst, e)
    return copied


def _build_zip(zip_path, copied):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, abs_src in copied:
            try:
                zf.write(abs_src, arcname)
            except OSError as e:
                logger.debug("Failed to zip %s: %s", abs_src, e)


# ============================================================
# Main entry point
# ============================================================

def build_validity_report(slug):
    """Generate the report HTML + the references bundle (folder + zip).

    Returns: (html: str, html_path: str, zip_path: str)
    Side effects: wipes and recreates projects/<slug>/validity-report/.
    """
    import project_store
    proj = project_store.get_project(slug)
    if proj is None:
        raise ValueError(f"Project not found: {slug}")
    project_dir = os.path.join(PROJECTS_DIR, slug)

    citations = proj.get("citations") or []
    results = proj.get("results") or []
    parsed_refs = proj.get("parsed_refs") or []
    claim_checks = proj.get("claim_checks") or {}
    tex_content = proj.get("tex_content") or ""

    refs_by_key = {r.get("bib_key"): r for r in results}
    parsed_by_key = {r.get("bib_key"): r for r in parsed_refs}

    # ---- 1. Build per-citation rows (one per occurrence) ----
    rows = []
    for idx, citation in enumerate(citations):
        bib_key = citation.get("bib_key")
        ref = refs_by_key.get(bib_key)
        ck_key = citation.get("claim_check_key")
        cc = claim_checks.get(ck_key) if ck_key else None
        severity = _classify(citation, ref, cc)
        rows.append({
            "idx": idx,
            "citation": citation,
            "ref": ref,
            "parsed_ref": parsed_by_key.get(bib_key),
            "claim_check": cc,
            "severity": severity,
        })

    # ---- 2. Bucket and sort ----
    problematic = [r for r in rows if r["severity"] not in ("partial", "clean")]
    partial     = [r for r in rows if r["severity"] == "partial"]
    clean       = [r for r in rows if r["severity"] == "clean"]

    problematic.sort(key=lambda r: (_SEVERITY_RANK[r["severity"]],
                                    r["citation"].get("line") or 0, r["idx"]))
    partial.sort(key=lambda r: (r["citation"].get("line") or 0, r["idx"]))
    clean.sort(key=lambda r: (r["citation"].get("line") or 0, r["idx"]))

    # ---- 3. Identify which refs need their files copied ----
    keys_with_files = {r["citation"].get("bib_key") for r in problematic + partial
                       if r["citation"].get("bib_key")}

    # ---- 4. Wipe + recreate the validity-report folder ----
    out_dir = os.path.join(project_dir, "validity-report")
    refs_dir = os.path.join(out_dir, "references")
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(refs_dir, exist_ok=True)

    # ---- 5. Copy linked files + build references.zip ----
    copied = _copy_files_for_keys(project_dir, refs_dir, results, keys_with_files)
    zip_path = os.path.join(out_dir, "references.zip")
    _build_zip(zip_path, copied)
    zip_size = os.path.getsize(zip_path) if os.path.isfile(zip_path) else 0

    # ---- 6. Compute summary stats ----
    summary = _build_summary_stats(proj, rows)

    # ---- 7. Render HTML ----
    html_content = _render_html(
        slug=slug, proj=proj, summary=summary,
        problematic=problematic, partial=partial, clean=clean,
        tex_content=tex_content, project_dir=project_dir, zip_size=zip_size,
    )
    html_path = os.path.join(out_dir, f"{slug}_report.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    return html_content, html_path, zip_path


# ============================================================
# Summary stats
# ============================================================

def _build_summary_stats(proj, rows):
    results = proj.get("results") or []
    total_refs = len(results)
    total_cites = len(rows)

    # Source-type counts (per reference)
    src = {"pdf": 0, "html": 0, "abstract": 0, "broken": 0, "none": 0}
    for r in results:
        st = r.get("status")
        if st == "found_pdf": src["pdf"] += 1
        elif st == "found_web_page": src["html"] += 1
        elif st == "found_abstract": src["abstract"] += 1
        elif st == "bib_url_unreachable": src["broken"] += 1
        else: src["none"] += 1

    # Identity counts (per reference)
    idn = {"matched": 0, "not_matched": 0, "unverifiable": 0,
           "manual": 0, "unchecked": 0}
    for r in results:
        rm = r.get("ref_match")
        if not rm: idn["unchecked"] += 1
        elif rm.get("verdict") == "matched": idn["matched"] += 1
        elif rm.get("verdict") == "not_matched": idn["not_matched"] += 1
        elif rm.get("verdict") in ("manual_matched", "manual_not_matched"): idn["manual"] += 1
        else: idn["unverifiable"] += 1

    # Claim counts (per citation)
    cl = {"supported": 0, "partial": 0, "not_supported": 0, "unknown": 0, "unchecked": 0}
    for row in rows:
        cc = row["claim_check"]
        if not cc: cl["unchecked"] += 1
        else: cl[cc.get("verdict", "unknown")] = cl.get(cc.get("verdict", "unknown"), 0) + 1

    needs_attention = sum(1 for row in rows
                          if row["severity"] not in ("clean",))

    return {
        "total_refs": total_refs, "total_cites": total_cites,
        "src": src, "identity": idn, "claim": cl,
        "needs_attention": needs_attention,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


# ============================================================
# HTML rendering
# ============================================================

def _esc(s):
    """HTML-escape; coerce None to empty string."""
    return html.escape(str(s)) if s is not None else ""


def _fmt_size(n):
    """Human-friendly file size."""
    if n is None:
        return ""
    n = int(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n / 1024 ** (' KMG'.index(unit[0])):.1f} {unit}"
        n //= 1
    return f"{n} B"


def _fmt_bytes(n):
    """Cleaner byte formatter."""
    if n is None: return ""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _anchor_id(idx, bib_key):
    """Stable HTML anchor id for a citation block."""
    safe = re.sub(r"[^\w-]", "_", str(bib_key or "x"))[:40]
    return f"cite-{idx}-{safe}"


def _render_paragraph_with_marker(tex_content, citation):
    """Return ONLY the single paragraph containing the citation, with the
    \\cite{...} command replaced by a [CITE:key] marker for visual location.

    Uses tex_parser.extract_claim_context which finds proper paragraph
    boundaries (between blank lines), so we don't bleed into neighbouring
    paragraphs even when the cite is near the start/end of one.
    """
    if not tex_content:
        return ""
    try:
        from tex_parser import extract_claim_context
        ctx = extract_claim_context(tex_content, citation,
                                    max_paragraph_chars=4000,
                                    max_sentence_chars=1500)
        # Prefer the cleaned paragraph (LaTeX commands stripped) so the report
        # shows readable prose, not raw \\cite{...} \\ref{...} noise.
        para = ctx.get("paragraph_clean") or ctx.get("paragraph") or ""
    except Exception as e:
        logger.debug("extract_claim_context failed for %s: %s",
                     citation.get("bib_key"), e)
        return ""
    bib_key = citation.get("bib_key", "?")
    cite_cmd = citation.get("cite_command") or f"\\cite{{{bib_key}}}"
    marker = f"[CITE:{bib_key}]"
    # The cleaned paragraph may have already replaced \cite with [CITE:...];
    # try the raw form first, then the [CITE:...] form, otherwise leave as-is.
    if cite_cmd in para:
        para = para.replace(cite_cmd, marker, 1)
    elif f"[CITE:{bib_key}]" in para:
        pass  # already marked by tex_parser
    return para.strip()


def _file_links_html(files, slug):
    """Render clickable links for the reference's local files (PDF, MD, HTML, ...).
    All links point to references/<filename> (relative to the report HTML)."""
    if not files:
        return '<span class="muted">no local files</span>'
    parts = []
    label_map = {"pdf": "PDF", "md": "Markdown", "page": "HTML",
                 "abstract": "Abstract", "pasted": "Pasted content"}
    for fkey, fname in files.items():
        if not fname: continue
        href = f"references/{_esc(fname)}"
        label = label_map.get(fkey, fkey.title())
        parts.append(
            f'<a href="{href}" target="_blank" rel="noopener" class="src-link">'
            f'<span class="src-link__label">{_esc(label)}</span> '
            f'<span class="src-link__name">{_esc(fname)}</span></a>'
        )
    return " · ".join(parts)


def _download_log_trace_html(log):
    """v6.1 §11.11 + §12.3 — collapsed per-tier trace for failed-download
    debugging. Returns empty string when no log or the log is only a single
    successful 'direct' attempt (most common case)."""
    if not log or (len(log) == 1 and log[0].get("ok") and log[0].get("tier") == "direct"):
        return ""
    lines = []
    lines.append('<details class="src__log"><summary>'
                 + str(len(log)) + ' download attempt' + ("s" if len(log) != 1 else "")
                 + ' (click to expand)</summary>')
    lines.append('<table class="src__log-table">')
    for entry in log:
        ok = entry.get("ok")
        icon = "✓" if ok else "✗"
        cls = "ok" if ok else "fail"
        tier = _esc(entry.get("tier") or "?")
        status = entry.get("http_status")
        kind = entry.get("kind") or ""
        elapsed = entry.get("elapsed_ms") or 0
        url = entry.get("final_url") or ""
        reason = (f"HTTP {status}" if status else "") + (f" {kind}" if kind else "")
        lines.append(
            f'<tr class="src__log-row src__log-row--{cls}">'
            f'<td class="src__log-icon">{icon}</td>'
            f'<td class="src__log-tier">{tier}</td>'
            f'<td class="src__log-reason">{_esc(reason.strip() or "ok")}</td>'
            f'<td class="src__log-elapsed">{elapsed} ms</td>'
            f'<td class="src__log-url">' + (f'<a href="{_esc(url)}" target="_blank" rel="noopener">{_esc(url[:60])}</a>' if url else '') + '</td>'
            f'</tr>'
        )
    lines.append('</table></details>')
    return "".join(lines)


def _identity_block_html(rm):
    """Render the identity check as two colored pills (TITLE OK / DOES NOT MATCH
    and AUTHORS OK / DO NOT MATCH) plus the LLM evidence on a new row.
    No "IDENTITY CHECK" label or emoji — the colored pills carry the meaning.
    Returns "" if rm is empty/None."""
    if not rm:
        return ""
    v = rm.get("verdict") or "unverifiable"
    cls_map = {
        "matched":             "matched",
        "not_matched":         "not_matched",
        "manual_matched":      "manual",
        "manual_not_matched":  "manual_bad",
        "unverifiable":        "unverifiable",
    }
    cls = cls_map.get(v, "unverifiable")

    def _pill(field_name, found):
        """Return a colored pill: green OK, red DOES NOT MATCH, neutral N/A."""
        if found is True:
            return f'<span class="id-pill id-pill--ok">{field_name} OK</span>'
        if found is False:
            verb = "DOES NOT MATCH" if field_name == "TITLE" else "DO NOT MATCH"
            return f'<span class="id-pill id-pill--bad">{field_name} {verb}</span>'
        # None/unknown — neutral pill (LLM couldn't see the byline, etc.)
        return f'<span class="id-pill id-pill--unknown">{field_name} NOT VISIBLE</span>'

    title_pill = _pill("TITLE", rm.get("title_found"))
    authors_pill = _pill("AUTHORS", rm.get("authors_found"))
    evidence = _esc(rm.get("evidence") or "")
    return (
        f'<div class="check check--identity check--{cls}">'
        f'<div class="check__head">{title_pill} {authors_pill}</div>'
        + (f'<div class="check__evidence">{evidence}</div>' if evidence else '')
        + '</div>'
    )


def _claim_block_html(cc):
    """Render the CLAIM CHECK section: header line (verdict + confidence) +
    LLM explanation on a new row + optional evidence quote on its own line."""
    if not cc:
        return ('<div class="check check--claim check--unchecked">'
                '<span class="check__title">? CLAIM CHECK</span> · Not yet checked.'
                '</div>')
    v = (cc.get("verdict") or "unknown").lower()
    icon_cls_label = {
        "supported":     ("✓",  "supported",     "SUPPORTED"),
        "partial":       ("⚠",  "partial",       "PARTIAL"),
        "not_supported": ("❌", "not_supported", "NOT SUPPORTED"),
        "unknown":       ("?",  "unknown",       "UNKNOWN"),
    }
    icon, cls, label = icon_cls_label.get(v, ("?", "unknown", v.upper()))
    conf = cc.get("confidence")
    conf_str = f" · confidence: {conf:.2f}" if isinstance(conf, (int, float)) else ""
    explanation = _esc(cc.get("explanation") or "")
    quote = _esc(cc.get("evidence_quote") or "")
    block = (
        f'<div class="check check--claim check--{cls}">'
        f'<div class="check__head"><span class="check__title">{icon} CLAIM CHECK</span>'
        f' · <strong>{_esc(label)}</strong>{_esc(conf_str)}</div>'
        + (f'<div class="check__evidence">{explanation}</div>' if explanation else '')
        + '</div>'
    )
    if quote:
        block += (
            f'<div class="check check--claim check--{cls} check__quote">'
            f'<q>{quote}</q></div>'
        )
    return block


def _excerpt_block_html(project_dir, bib_key):
    """Collapsed excerpt (first ~6000 chars of .md body — what the identity LLM saw)."""
    excerpt = _md_excerpt_for(project_dir, bib_key)
    if not excerpt:
        return ""
    return (
        '<details class="excerpt"><summary>Excerpt sent to identity-check LLM '
        '(first ~6,000 chars of the .md body)</summary>'
        '<pre class="excerpt__body">' + _esc(excerpt) + '</pre></details>'
    )


def _citation_block_html(row, position, total, project_dir, tex_content,
                          tex_filename, slug):
    """Render one per-occurrence block."""
    citation = row["citation"]
    ref = row["ref"]
    parsed = row["parsed_ref"]
    cc = row["claim_check"]
    severity = row["severity"]
    meta = SEVERITY_META[severity]
    bib_key = citation.get("bib_key", "?")
    line = citation.get("line", "?")

    paragraph = _render_paragraph_with_marker(tex_content, citation)

    # Bib record (raw_bib if available, else parsed dict)
    raw_bib = (ref or {}).get("raw_bib") or (parsed or {}).get("raw_bib") or ""
    if not raw_bib and parsed:
        raw_bib = (
            f"@{parsed.get('entry_type','article')}{{{bib_key},\n"
            f"  title  = {{{parsed.get('title') or ''}}},\n"
            f"  author = {{{parsed.get('authors') or ''}}},\n"
            f"  year   = {{{parsed.get('year') or ''}}},\n}}"
        )

    # Downloaded source line
    src_html = ""
    if ref:
        if ref.get("status") == "bib_url_unreachable":
            failure = ref.get("bib_url_failure") or {}
            code = failure.get("http_status") or "?"
            kind = failure.get("kind") or "unknown"
            src_html = (
                '<div class="src__line src__broken"><strong>Status:</strong> '
                'bib URL unreachable (HTTP ' + _esc(code) + ' / ' + _esc(kind) + ') · '
                '<a href="' + _esc(ref.get("url") or "#") + '" target="_blank" rel="noopener">'
                + _esc(ref.get("url") or "") + '</a></div>'
            )
        else:
            type_label = {"found_pdf": "PDF", "found_abstract": "Abstract",
                          "found_web_page": "Web page", "not_found": "Not found"}.get(
                ref.get("status") or "", ref.get("status") or "?")
            remote = ref.get("pdf_url") or ref.get("url") or ""
            sources = ", ".join(ref.get("sources") or [])
            # v6.1 §12.5 — tier + explainer + optional download log trace
            pdf_origin = (ref.get("files_origin") or {}).get("pdf") or {}
            tier = pdf_origin.get("tier")
            tier_line = ""
            if tier:
                when = (pdf_origin.get("captured_at") or "")[:10]  # YYYY-MM-DD
                tier_line = (
                    '<div class="src__line src__tier"><strong>Downloaded via:</strong> '
                    + _esc(tier)
                    + (' · ' + _esc(when) if when else '')
                    + '</div>'
                )
                explainer = TIER_EXPLAINERS.get(tier)
                if explainer:
                    tier_line += ('<div class="src__explainer">ℹ '
                                    + _esc(explainer) + '</div>')
            src_html = (
                '<div class="src__line"><strong>Source:</strong> '
                + _esc(type_label)
                + (' · <a href="' + _esc(remote) + '" target="_blank" rel="noopener">'
                   + _esc(remote) + '</a>' if remote else '')
                + (' · sources used: ' + _esc(sources) if sources else '')
                + '</div>'
                + tier_line
                + '<div class="src__line"><strong>Local files:</strong> '
                + _file_links_html(ref.get("files"), slug) + '</div>'
                + _download_log_trace_html(ref.get("download_log"))
            )
    else:
        src_html = '<div class="src__line src__missing">No reference data — citation key not found in .bib.</div>'

    # Identity + claim blocks (only if relevant)
    identity_html = _identity_block_html((ref or {}).get("ref_match"))
    claim_html = _claim_block_html(cc)
    excerpt_html = (_excerpt_block_html(project_dir, bib_key)
                    if ref and (ref.get("files") or {}).get("md") else "")

    line_label = (f"{tex_filename}:{line}" if tex_filename and line else f"L{line}")

    return f'''
<section class="cite cite--{severity}" id="{_anchor_id(citation.get('idx', 0) if citation.get('idx') is not None else position-1, bib_key)}">
  <header class="cite__head">
    <span class="cite__badge cite__badge--{severity}">
      {meta['emoji']} {_esc(meta['label'])}
    </span>
    <span class="cite__loc">
      <a class="cite__line" href="#" data-line="{_esc(line)}" title="Click to copy {_esc(line_label)}">{_esc(line_label)}</a>
      &nbsp;<code>\\cite{{{_esc(bib_key)}}}</code>
    </span>
    <span class="cite__pos">#{position} of {total}</span>
  </header>

  <div class="cite__body">
    <div class="cite__section">
      <div class="cite__section-title">Bib entry</div>
      <pre class="cite__bib">{_esc(raw_bib) if raw_bib else '<span class="muted">no bib entry</span>'}</pre>
    </div>

    {identity_html}
    {excerpt_html}
    {claim_html}

    <div class="cite__section">
      <div class="cite__section-title">Citation context (paragraph)</div>
      <pre class="cite__paragraph">{_esc(paragraph)}</pre>
    </div>

    <div class="cite__section">
      <div class="cite__section-title">Downloaded source</div>
      {src_html}
    </div>
  </div>
</section>
'''


def _summary_html(summary, zip_size):
    s = summary
    src = s["src"]; idn = s["identity"]; cl = s["claim"]
    return f'''
<section class="summary">
  <h2>Summary</h2>
  <div class="summary__head">
    {s["total_refs"]} references · {s["total_cites"]} citations · checked {_esc(s["checked_at"])}
  </div>
  <ul class="summary__rows">
    <li><strong>References by source:</strong>
      PDF {src["pdf"]} · HTML {src["html"]} · Abstract {src["abstract"]} ·
      Broken URL {src["broken"]} · None {src["none"]}</li>
    <li><strong>Identity verification:</strong>
      ✓ matched {idn["matched"]} · ✗ NOT matched {idn["not_matched"]} ·
      ✎ manual {idn["manual"]} · ? unverifiable {idn["unverifiable"]} ·
      unchecked {idn["unchecked"]}</li>
    <li><strong>Claim support:</strong>
      ✓ supported {cl.get("supported",0)} · ⚠ partial {cl.get("partial",0)} ·
      ✗ not supported {cl.get("not_supported",0)} · ? unknown {cl.get("unknown",0)} ·
      unchecked {cl.get("unchecked",0)}</li>
  </ul>
  <div class="summary__attention">
    ⚠ <strong>CITATIONS NEEDING ATTENTION: {s["needs_attention"]}</strong>
    &nbsp;<a href="#first-problematic">jump to first problematic →</a>
  </div>
  <div class="summary__zip">
    📦 <a href="references.zip" download>Download references bundle (references.zip — {_fmt_bytes(zip_size)})</a>
    <span class="muted">— extract next to this HTML on your laptop to make every source link work offline.</span>
  </div>
</section>
'''


def _methodology_html(proj):
    cc = get_claim_check_settings()
    rm = get_reference_match_settings()
    return f'''
<footer class="methodology">
  <h2>Methodology</h2>
  <ul>
    <li><strong>Lookup pipeline:</strong> CrossRef → Unpaywall → OpenAlex → Semantic Scholar
        → arXiv → Google Search → Google Scholar</li>
    <li><strong>Identity model:</strong> {_esc(rm.get("openai_model"))} (excerpt = first {rm.get("max_chars",6000):,} chars of body)</li>
    <li><strong>Claim model:</strong> {_esc(cc.get("openai_model"))} (max {cc.get("max_ref_chars",100000):,} chars of reference)</li>
  </ul>
  <h3>Known limitations</h3>
  <ul>
    <li>LLM-based checks are heuristic — false positives and false negatives occur.</li>
    <li>A "matched" identity does not certify factual correctness of the claim.</li>
    <li>Manually-flagged verdicts (✎) reflect the author's judgment, not the LLM.</li>
  </ul>
</footer>
'''


def _clean_one_liner_html(row):
    citation = row["citation"]
    ref = row["ref"] or {}
    line = citation.get("line", "?")
    bib_key = citation.get("bib_key", "?")
    title = ref.get("title") or ""
    return (f'<li><span class="clean__loc">L{_esc(line)}</span> '
            f'<code>\\cite{{{_esc(bib_key)}}}</code> '
            f'<span class="clean__title">— {_esc(title)}</span></li>')


def _render_html(slug, proj, summary, problematic, partial, clean,
                  tex_content, project_dir, zip_size):
    name = proj.get("name") or slug
    bib_filename = proj.get("bib_filename") or "—"
    tex_filename = proj.get("tex_filename") or "—"

    def _blocks(rows, anchor_first=False):
        out = []
        total = len(rows)
        for i, row in enumerate(rows, 1):
            row_for_block = dict(row); row_for_block["citation"] = dict(row["citation"])
            row_for_block["citation"]["idx"] = row["idx"]
            html_block = _citation_block_html(
                row_for_block, position=i, total=total,
                project_dir=project_dir, tex_content=tex_content,
                tex_filename=tex_filename if tex_filename != "—" else None,
                slug=slug,
            )
            if anchor_first and i == 1:
                # Insert the "first-problematic" anchor on the first block
                html_block = '<a id="first-problematic"></a>' + html_block
            out.append(html_block)
        return "\n".join(out)

    problematic_html = _blocks(problematic, anchor_first=True) if problematic else \
        '<p class="empty">🎉 No problematic citations.</p>'
    partial_html = _blocks(partial) if partial else \
        '<p class="empty">No partial citations.</p>'
    clean_html = ("<ol class=\"clean__list\">"
                  + "".join(_clean_one_liner_html(r) for r in clean)
                  + "</ol>") if clean else '<p class="empty">No clean citations.</p>'

    css = _css()
    js = _js()
    title = f"Validity Report — {_esc(name)}"

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<header class="page-head">
  <h1>Citation Validity Report</h1>
  <div class="page-head__meta">
    <strong>{_esc(name)}</strong>
    · .bib: <code>{_esc(bib_filename)}</code>
    · .tex: <code>{_esc(tex_filename)}</code>
    · generated {_esc(summary["checked_at"])}
  </div>
</header>

{_summary_html(summary, zip_size)}

<section class="bucket bucket--problematic">
  <h2>Problematic citations <span class="bucket__count">({len(problematic)})</span></h2>
  {problematic_html}
</section>

<section class="bucket bucket--partial">
  <h2>Partial citations <span class="bucket__count">({len(partial)})</span></h2>
  <p class="bucket__hint">Tangentially related references — re-read each and decide whether to <strong>strengthen</strong> the claim or <strong>remove</strong> the cite.</p>
  {partial_html}
</section>

<section class="bucket bucket--clean">
  <h2>Clean citations <span class="bucket__count">({len(clean)})</span></h2>
  <details>
    <summary>Show {len(clean)} clean citations</summary>
    {clean_html}
  </details>
</section>

{_methodology_html(proj)}
<script>{js}</script>
</body>
</html>'''


# ============================================================
# Inline CSS + JS (kept here so the report is single-file)
# ============================================================

def _css():
    return """
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       max-width: 980px; margin: 1.5rem auto; padding: 0 1rem;
       color: #1f2937; line-height: 1.55; background: #fafafa; }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
h1 { font-size: 1.6rem; margin: 0 0 0.4rem; }
h2 { font-size: 1.2rem; margin-top: 1.6rem; border-bottom: 1px solid #e5e7eb; padding-bottom: 0.3rem; }
h3 { font-size: 1rem; margin-top: 1rem; }
a { color: #1d4ed8; }
.muted { color: #6b7280; font-style: italic; }
.empty { color: #6b7280; padding: 0.7rem 0; }

.page-head { padding-bottom: 0.6rem; border-bottom: 2px solid #e5e7eb; }
.page-head__meta { color: #4b5563; font-size: 0.88rem; }

.summary { background: #fff; border: 1px solid #e5e7eb; border-radius: 6px;
           padding: 0.7rem 1rem; margin-top: 1rem; }
.summary__head { font-weight: 600; margin-bottom: 0.4rem; color: #374151; }
.summary__rows { list-style: none; padding: 0; margin: 0; font-size: 0.92rem; }
.summary__rows li { padding: 0.15rem 0; }
.summary__attention { margin-top: 0.6rem; padding: 0.5rem 0.7rem;
                       background: #fef3c7; border-left: 4px solid #f59e0b;
                       font-weight: 600; }
.summary__zip { margin-top: 0.6rem; padding: 0.5rem 0.7rem;
                 background: #ecfdf5; border-left: 4px solid #10b981; }

.bucket__count { color: #6b7280; font-size: 0.9rem; font-weight: 400; }
.bucket__hint { color: #4b5563; font-size: 0.88rem; padding: 0.3rem 0; }

.cite { background: #fff; border: 1px solid #e5e7eb; border-radius: 6px;
        margin: 0.7rem 0; padding: 0.7rem 0.9rem;
        border-left: 5px solid #94a3b8; }
.cite--parse_error, .cite--missing_key, .cite--broken_url, .cite--no_md
                                                          { border-left-color: #dc2626; background: #fffbfb; }
.cite--identity_not_matched, .cite--claim_not_supported   { border-left-color: #ef4444; }
.cite--partial                                            { border-left-color: #f59e0b; }
.cite--identity_unverifiable                              { border-left-color: #f59e0b; }

.cite__head { display: flex; align-items: center; gap: 0.6rem;
               flex-wrap: wrap; padding-bottom: 0.4rem;
               border-bottom: 1px dashed #e5e7eb; margin-bottom: 0.5rem; }
.cite__badge { font-weight: 700; font-size: 0.78rem; padding: 0.15rem 0.55rem;
                border-radius: 999px; background: #f3f4f6; color: #1f2937; }
.cite__badge--parse_error, .cite__badge--missing_key, .cite__badge--broken_url,
.cite__badge--no_md
   { background: #fee2e2; color: #7f1d1d; }
.cite__badge--identity_not_matched, .cite__badge--claim_not_supported
   { background: #fee2e2; color: #7f1d1d; }
.cite__badge--partial, .cite__badge--identity_unverifiable
   { background: #fef3c7; color: #92400e; }
.cite__loc { font-size: 0.92rem; }
.cite__line { color: #4338ca; text-decoration: none; font-weight: 600; }
.cite__line:hover { text-decoration: underline; }
.cite__pos { margin-left: auto; color: #9ca3af; font-size: 0.82rem; }

.cite__section { margin-top: 0.5rem; }
.cite__section-title { font-size: 0.78rem; color: #6b7280;
                        text-transform: uppercase; letter-spacing: 0.04em;
                        font-weight: 600; margin-bottom: 0.2rem; }
.cite__paragraph, .cite__bib { background: #f9fafb; border: 1px solid #e5e7eb;
                                 border-radius: 4px; padding: 0.55rem 0.7rem;
                                 white-space: pre-wrap; word-wrap: break-word;
                                 font-size: 0.85rem; max-height: 24em; overflow: auto; }
.cite__bib { background: #f1f5f9; }

.src__line { padding: 0.2rem 0; font-size: 0.88rem; }
.src__broken { color: #b91c1c; }
.src__missing { color: #b91c1c; font-style: italic; }
.src-link { display: inline-block; margin-right: 0.5rem;
             padding: 0.1rem 0.45rem; border-radius: 4px;
             background: #eef2ff; color: #3730a3; text-decoration: none;
             font-size: 0.82rem; }
.src-link:hover { background: #e0e7ff; }
.src-link__label { font-weight: 700; }
.src-link__name  { font-family: ui-monospace, monospace; font-size: 0.78rem; opacity: 0.8; }

.src__tier { margin-top: 0.2rem; }
.src__explainer { margin-top: 0.2rem; padding: 0.3rem 0.6rem;
                   background: #eff6ff; border-left: 3px solid #3b82f6;
                   font-size: 0.82rem; color: #1e3a8a; }
.src__log { margin-top: 0.4rem; }
.src__log summary { cursor: pointer; color: #6b7280; font-size: 0.82rem; padding: 0.2rem 0; }
.src__log-table { width: 100%; border-collapse: collapse; font-size: 0.76rem;
                   margin-top: 0.3rem; font-family: ui-monospace, monospace; }
.src__log-row--ok   .src__log-icon   { color: #047857; font-weight: 700; }
.src__log-row--fail .src__log-icon   { color: #b91c1c; font-weight: 700; }
.src__log-row td { padding: 0.15rem 0.4rem; vertical-align: top;
                    border-bottom: 1px dashed #e5e7eb; }
.src__log-tier   { font-weight: 600; }
.src__log-elapsed{ color: #6b7280; }
.src__log-url a  { color: #3730a3; }

.check { margin-top: 0.5rem; padding: 0.5rem 0.7rem; border-radius: 4px;
          background: #f9fafb; border-left: 3px solid #94a3b8; }
.check__head { display: flex; gap: 0.5rem; align-items: baseline; }
.check__title { font-weight: 700; font-size: 0.9rem; }
.check__meta { color: #6b7280; font-size: 0.78rem; }
.check__verdict { margin-top: 0.2rem; font-size: 0.92rem; }
.check__evidence, .check__quote { margin-top: 0.3rem; font-size: 0.86rem; color: #374151; }
.check__quote q { font-style: italic; }
.id-pill { display: inline-block; padding: 0.15rem 0.55rem; border-radius: 999px;
            font-size: 0.78rem; font-weight: 700; letter-spacing: 0.02em;
            margin-right: 0.4rem; }
.id-pill--ok      { background: #d1fae5; color: #065f46; border: 1px solid #a7f3d0; }
.id-pill--bad     { background: #fee2e2; color: #991b1b; border: 1px solid #fca5a5; }
.id-pill--unknown { background: #f3f4f6; color: #4b5563; border: 1px solid #d1d5db; }

.check--matched, .check--supported       { background: #ecfdf5; border-left-color: #10b981; }
.check--not_matched, .check--not_supported { background: #fef2f2; border-left-color: #ef4444; }
.check--manual                           { background: #eef2ff; border-left-color: #6366f1; }
.check--manual_bad                       { background: #fdf2f8; border-left-color: #ec4899; }
.check--unverifiable, .check--partial, .check--unknown
                                          { background: #fffbeb; border-left-color: #f59e0b; }
.check--unchecked                         { background: #f8fafc; border-left-color: #94a3b8; }

.excerpt { margin-top: 0.5rem; }
.excerpt summary { cursor: pointer; color: #6b7280; font-size: 0.85rem; padding: 0.2rem 0; }
.excerpt__body { background: #fafafa; border: 1px dashed #d1d5db;
                  padding: 0.5rem; max-height: 30em; overflow: auto;
                  font-size: 0.78rem; white-space: pre-wrap; }

.cite__fix { margin-top: 0.7rem; padding: 0.5rem 0.7rem; background: #eff6ff;
              border-left: 3px solid #3b82f6; font-size: 0.92rem; }

.clean__list { padding-left: 1.5rem; font-size: 0.88rem; color: #4b5563; }
.clean__loc { color: #4338ca; font-weight: 600; }
.clean__title { color: #6b7280; }

.methodology { margin-top: 2rem; padding-top: 0.8rem; border-top: 2px solid #e5e7eb;
                color: #4b5563; font-size: 0.88rem; }
.methodology ul { margin: 0.3rem 0 0.8rem 1.2rem; padding: 0; }

@media print {
  body { background: #fff; max-width: none; }
  .cite, .summary, .methodology { box-shadow: none; }
  .excerpt[open] .excerpt__body { max-height: none; overflow: visible; }
}
"""


def _js():
    """Tiny enhancement: clicking a line number copies it to the clipboard."""
    return """
document.querySelectorAll('.cite__line').forEach(function (a) {
  a.addEventListener('click', function (ev) {
    ev.preventDefault();
    var text = a.textContent.trim();
    if (navigator.clipboard) {
      navigator.clipboard.writeText(text).then(function () {
        var orig = a.textContent;
        a.textContent = '✓ copied: ' + text;
        setTimeout(function () { a.textContent = orig; }, 1200);
      });
    }
  });
});
"""
