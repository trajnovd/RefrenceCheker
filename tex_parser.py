import re

# Match \cite, \citep, \citet, \parencite, \textcite, \autocite, \fullcite, etc.
# With optional arguments: \cite[p.~42]{key} or \cite[see][p.~42]{key}
_CITE_RE = re.compile(
    r'\\(?:cite[tp]?|parencite|textcite|autocite|fullcite|nocite)'
    r'(?:\s*\[[^\]]*\])*'   # optional arguments like [p.~42]
    r'\s*\{([^}]+)\}',      # capture the {key1,key2,...}
    re.DOTALL
)

# Hard breaks for paragraph boundary detection.
_PARA_BOUNDARY_RE = re.compile(
    r'(?:\n\s*\n)'                                                    # blank line
    r'|\\(?:section|subsection|subsubsection|paragraph|chapter|part)\*?\s*\{'  # sectioning
    r'|\\(?:begin|end)\s*\{(?:figure|table|equation|align|itemize|enumerate|abstract|quote|verbatim|lstlisting|tabular)\*?\}',
    re.DOTALL,
)

# Sentence terminators — but not preceded by common abbreviations.
# We do this with a simple right-walk, checking each candidate.
_ABBREV_RE = re.compile(r'(?:e\.g|i\.e|cf|etc|al|vs|Fig|Eq|Ref|No|Ch|Sec|Tab|pp?)\.?$', re.IGNORECASE)


def parse_tex_citations(tex_content):
    """Parse LaTeX content, return list of citation occurrences in document order.

    Each entry: {"bib_key", "position", "line", "cite_command", "context_before", "context_after"}
    Multi-key cites like \\cite{a,b,c} produce one entry per key.
    """
    citations = []

    # Precompute line starts for line number lookup
    line_starts = [0]
    for i, ch in enumerate(tex_content):
        if ch == '\n':
            line_starts.append(i + 1)

    def _get_line(pos):
        lo, hi = 0, len(line_starts) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if line_starts[mid] <= pos:
                lo = mid + 1
            else:
                hi = mid - 1
        return lo  # 1-based line number

    for match in _CITE_RE.finditer(tex_content):
        keys_str = match.group(1)
        cite_command = match.group(0)
        pos = match.start()
        line = _get_line(pos)

        # Context: ~200 chars before and after
        ctx_start = max(0, pos - 200)
        ctx_end = min(len(tex_content), match.end() + 200)
        context_before = tex_content[ctx_start:pos]
        context_after = tex_content[match.end():ctx_end]

        # Split multi-key cites
        keys = [k.strip() for k in keys_str.split(',') if k.strip()]
        for key in keys:
            citations.append({
                "bib_key": key,
                "position": pos,
                "end_position": match.end(),
                "line": line,
                "cite_command": cite_command,
                "context_before": context_before,
                "context_after": context_after,
            })

    return citations


# ============================================================
# v4: claim-context extraction (paragraph + sentence per cite)
# ============================================================

def extract_claim_context(tex_content, citation,
                          max_paragraph_chars=4000,
                          max_sentence_chars=1500):
    """For one citation entry, return its containing sentence and paragraph.

    citation: dict with at least "position" (start) and "end_position".
    Returns:
      {
        "sentence":         <raw substring>,
        "paragraph":        <raw substring>,
        "sentence_clean":   <LaTeX-stripped>,
        "paragraph_clean":  <LaTeX-stripped>,
      }
    """
    pos = citation.get("position", 0)
    end = citation.get("end_position", pos)
    n = len(tex_content)

    # --- paragraph boundaries ---
    para_start = 0
    para_end = n
    for m in _PARA_BOUNDARY_RE.finditer(tex_content, 0, pos):
        para_start = m.end()
    m_after = _PARA_BOUNDARY_RE.search(tex_content, end)
    if m_after:
        para_end = m_after.start()

    paragraph = tex_content[para_start:para_end]
    if len(paragraph) > max_paragraph_chars:
        # Window centered on the citation, clamped to paragraph bounds.
        half = max_paragraph_chars // 2
        rel_pos = pos - para_start
        win_start = max(0, rel_pos - half)
        win_end = min(len(paragraph), win_start + max_paragraph_chars)
        win_start = max(0, win_end - max_paragraph_chars)
        paragraph = paragraph[win_start:win_end]

    # --- sentence boundaries ---
    sent_start = _walk_sentence_left(tex_content, pos)
    sent_end = _walk_sentence_right(tex_content, end)
    sentence = tex_content[sent_start:sent_end].strip()
    if not sentence or len(sentence) > max_sentence_chars:
        # Fallback: 500-char window centered on the citation.
        half = 250
        ws = max(0, pos - half)
        we = min(n, end + half)
        sentence = tex_content[ws:we].strip()

    return {
        "sentence": sentence,
        "paragraph": paragraph,
        "sentence_clean": clean_latex(sentence),
        "paragraph_clean": clean_latex(paragraph),
    }


def _is_abbreviation_terminator(text, i):
    """True if the terminator at text[i] is part of an abbreviation, not end-of-sentence.

    Checked via two heuristics:
    1. The chunk to the left matches a known abbreviation (e.g., 'e.g.', 'Fig.', 'al.').
    2. The next non-space char is lowercase or digit (e.g., 'e.g. as shown' -> 'a' lowercase).
    """
    left_chunk = text[max(0, i - 10):i + 1]
    if _ABBREV_RE.search(left_chunk):
        return True
    j = i + 1
    while j < len(text) and text[j] in ' \t':
        j += 1
    if j < len(text):
        nxt = text[j]
        if nxt.islower() or nxt.isdigit() or nxt == ',':
            return True
    return False


def _walk_sentence_left(text, pos):
    """Find the start of the sentence containing pos."""
    i = pos - 1
    while i > 0:
        ch = text[i]
        if ch in '.?!':
            if _is_abbreviation_terminator(text, i):
                i -= 1
                continue
            j = i + 1
            while j < len(text) and text[j] in ' \t\n':
                j += 1
            return j
        i -= 1
    return 0


def _walk_sentence_right(text, end):
    """Find the end (exclusive) of the sentence containing end."""
    i = end
    while i < len(text):
        ch = text[i]
        if ch in '.?!':
            if _is_abbreviation_terminator(text, i):
                i += 1
                continue
            return i + 1
        i += 1
    return len(text)


# --- Light LaTeX stripping (for LLM input only; raw text is kept for UI) ---

_COMMENT_RE = re.compile(r'(?<!\\)%.*?$', re.MULTILINE)
_INLINE_FORMAT_RE = re.compile(r'\\(?:textbf|textit|emph|texttt|underline|textsc|textrm|textsf|mathit|mathbf|mathrm)\s*\{([^{}]*)\}')
_REF_RE = re.compile(r'\\(?:ref|autoref|eqref|cref|Cref|pageref|nameref)\s*\{[^{}]*\}')
_GENERIC_CMD_RE = re.compile(r'\\[a-zA-Z]+\*?\s*\{([^{}]*)\}')
_BARE_CMD_RE = re.compile(r'\\[a-zA-Z]+\*?')
_WHITESPACE_RE = re.compile(r'\s+')


def clean_latex(text):
    """Strip LaTeX comments + most commands; keep cite-key placeholders."""
    if not text:
        return ""
    # 1. Drop comments (but keep escaped \%)
    text = _COMMENT_RE.sub('', text)
    # 2. Replace cite commands with [CITE:key1,key2] markers
    text = _CITE_RE.sub(lambda m: '[CITE:' + m.group(1).strip() + ']', text)
    # 3. Replace ref commands with [REF]
    text = _REF_RE.sub('[REF]', text)
    # 4. Replace inline formatting commands with their contents
    text = _INLINE_FORMAT_RE.sub(r'\1', text)
    # 5. Strip remaining \cmd{arg} → arg (best effort, runs once)
    text = _GENERIC_CMD_RE.sub(r'\1', text)
    # 6. Drop bare commands like \\, \LaTeX, \\noindent
    text = _BARE_CMD_RE.sub(' ', text)
    # 7. Drop stray braces
    text = text.replace('{', '').replace('}', '')
    # 8. Collapse whitespace
    text = _WHITESPACE_RE.sub(' ', text).strip()
    return text
