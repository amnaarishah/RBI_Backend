"""Parsing helpers for ASME BPVC II-D Table 1A / 1B rows extracted from the client's PDF.

A table row is split over several visual lines in the PDF (composition, product form,
spec, grade, UNS number, condition ...), so spans are assigned to the nearest line number
(anchor) by their vertical position instead of requiring them to share one y value.
"""
import re
from typing import Dict, List, Optional

FILLER = {'...', '…', '-', '–', '—', '�', '�', ''}
UNS_RE = re.compile(r'^[A-Z]\d{5}$')
SPEC_RE = re.compile(r'\b(S[AB](?:/[A-Z]{2,3})?|SFA|EN|JIS)\s*[-‐-―�]?\s*([A-Z]?\d[\w./]*(?:\s*-\s*\d+)?)')

PRODUCT_TYPES = [
    (r'bolting', 'Bolting'),
    (r'forg', 'Forging'),
    (r'cast', 'Casting'),
    (r'fitting', 'Fitting'),
    (r'pipe', 'Pipe'),
    (r'tube|tubing', 'Tube'),
    (r'plate|sheet|strip', 'Plate/Sheet'),
    (r'bar|rod|wire|shapes', 'Bar/Rod'),
]


def extract_rows(page) -> Dict[int, List[str]]:
    """Return {line_no: [text pieces in reading order]} for a table page.

    Every text span is assigned to the closest line-number anchor (nearest by y)."""
    spans = []
    for block in page.get_text('dict')['blocks']:
        if block['type'] != 0:
            continue
        for line in block['lines']:
            for sp in line['spans']:
                t = sp['text'].strip().replace('–', '-')
                if t:
                    x0, y0, x1, y1 = sp['bbox']
                    spans.append((t, x0, (y0 + y1) / 2))
    ints = [(int(t), x, y) for t, x, y in spans if t.isdigit() and 1 <= int(t) <= 45]
    if not ints:
        return {}
    # the line-number column is the x position that holds the most distinct line numbers
    # (a stray small integer elsewhere on the page must not define it)
    by_x = {}
    for n, x, y in ints:
        by_x.setdefault(round(x / 6), set()).add(n)
    col = max(by_x, key=lambda k: len(by_x[k]))
    anchors = {}
    for n, x, y in sorted(ints, key=lambda r: r[2]):
        if abs(round(x / 6) - col) <= 1 and n not in anchors:
            anchors[n] = y
    ys = sorted(anchors.values())
    gaps = sorted(b - a for a, b in zip(ys, ys[1:]) if b - a > 1)
    pitch = gaps[len(gaps) // 2] if gaps else 12.0          # typical distance between two lines
    reach = 2.2 * pitch                                      # a cell never extends further than this
    rows: Dict[int, list] = {n: [] for n in anchors}
    for t, x, y in spans:
        n = min(anchors, key=lambda k: abs(anchors[k] - y))
        if abs(anchors[n] - y) > reach:                      # page header / footer / footnotes
            continue
        # beyond the first / last line of the table only a little overhang is possible:
        # anything further out is the column-heading block or a footnote
        if (y < ys[0] and ys[0] - y > 0.8 * pitch) or (y > ys[-1] and y - ys[-1] > 0.8 * pitch):
            continue
        rows[n].append((round(y / 3), x, t))
    return {n: [t for _, _, t in sorted(v)] for n, v in rows.items()}


def product_type_of(text_before_spec: str) -> str:
    low = text_before_spec.lower()
    for pat, name in PRODUCT_TYPES:
        if re.search(pat, low):
            return name
    return 'Other'


def parse_material(pieces: List[str]) -> Optional[dict]:
    """Parse one specification row (without the leading line number)."""
    toks = [p for p in pieces[1:] if p.strip()] if pieces and pieces[0].isdigit() else list(pieces)
    s = ' '.join(toks).replace('�', ' ').replace('−', '-')
    s = re.sub(r'\s+', ' ', s)
    m = SPEC_RE.search(s)
    if not m:
        return None
    prefix = m.group(1)
    num = re.sub(r'\s+', '', m.group(2))
    clean_spec = f"{prefix}-{num}".upper()
    before = s[:m.start()].strip()
    after = s[m.end():].split()

    uns, grade_tokens, cond = None, [], []
    for tk in after:
        if tk in FILLER:
            continue
        if uns is None and UNS_RE.match(tk):
            uns = tk
            continue
        if uns is None and not grade_tokens:
            grade_tokens.append(tk)
        elif uns is None and grade_tokens[-1].rstrip('.').lower() in ('gr', 'grade', 'type', 'cl', 'class')                 and len(grade_tokens) == 1:
            grade_tokens.append(tk)          # "Gr. 70", "Type 304", "Cl. 1"
        else:
            cond.append(tk)
    # last two small integers after the UNS are P-No. and Group No. -> drop from condition text
    while cond and re.fullmatch(r'\d{1,2}', cond[-1]) and len(cond) > 0 and len(cond) <= 2:
        cond.pop()
    return {
        'clean_spec': clean_spec,
        'clean_grade': ' '.join(grade_tokens) or None,
        'uns_no': uns,
        'product_type': product_type_of(before),
        'condition_text': ' '.join(cond)[:200] or None,
        'description': s[:300],
    }
