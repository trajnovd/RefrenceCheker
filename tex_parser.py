import re

# Match \cite, \citep, \citet, \parencite, \textcite, \autocite, \fullcite, etc.
# With optional arguments: \cite[p.~42]{key} or \cite[see][p.~42]{key}
_CITE_RE = re.compile(
    r'\\(?:cite[tp]?|parencite|textcite|autocite|fullcite|nocite)'
    r'(?:\s*\[[^\]]*\])*'   # optional arguments like [p.~42]
    r'\s*\{([^}]+)\}',      # capture the {key1,key2,...}
    re.DOTALL
)


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
