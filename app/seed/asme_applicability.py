"""Read the "strength / applicability / notes" page of ASME II-D Table 1A and 1B (the page right after the
specification page of each 4-page group). For every line it gives:

  min tensile and min yield strength (ksi), the Section VIII-1 limit (a temperature in F, or NP = not
  permitted), the notes (G3, W6, ...) and, in Table 1B, the size/thickness range.

Columns are found from the header of each page (x position of "III", "VIII-1", "XII", "Notes"), not guessed.
"""
import re

LINE_X_MAX = 92          # the line number sits in the first column
REACH = 7.0              # a word belongs to the line number whose y is nearest, at most this far away


def _num(tok):
    try:
        return float(tok)
    except (TypeError, ValueError):
        return None


def parse_applicability_page(page, table):
    """{line_no: dict(min_tensile_ksi, min_yield_ksi, viii1_text, viii1_limit_f, viii1_permitted, notes_text,
    size_thickness)} for one strength page. Returns ({}, reason) if the header cannot be found."""
    words = [(w[0], (w[1] + w[3]) / 2, w[4].replace("�", "-").replace("–", "-")) for w in page.get_text("words")]

    hdr = next(((x, y) for x, y, t in words if t == "VIII-1"), None)
    if not hdr:
        return {}, "no VIII-1 header"
    hy = hdr[1]
    on_row = {t: x for x, y, t in words if abs(y - hy) < 3}
    if not all(k in on_row for k in ("III", "VIII-1", "XII", "Notes")):
        return {}, "incomplete header"
    x_i = on_row.get("I", on_row["III"] - 55)
    x_iii, x_8, x_xii, x_notes = on_row["III"], on_row["VIII-1"], on_row["XII"], on_row["Notes"]
    lo, hi = (x_iii + x_8) / 2, (x_8 + x_xii) / 2          # the VIII-1 column
    notes_from = x_notes - 60                              # chart numbers sit left of this, notes right of it
    first_col_end = x_i - 25                               # strengths (and 1B size / P-No.) are left of the I column

    anchors = [(y, int(t)) for x, y, t in words if x < LINE_X_MAX and t.isdigit() and 1 <= int(t) <= 45 and y > hy]
    anchors.sort()
    if not anchors:
        return {}, "no lines"

    cells = {n: [] for _, n in anchors}
    for x, y, t in words:
        if y <= hy or x < LINE_X_MAX - 1 and t.isdigit():
            continue
        y_near, n_near = min(anchors, key=lambda a: abs(a[0] - y))
        if abs(y_near - y) <= REACH:
            cells[n_near].append((x, t))

    out = {}
    for n, items in cells.items():
        items.sort()
        first = [t for x, t in items if x < first_col_end]
        v8 = [t for x, t in items if lo <= x < hi]
        notes = [t for x, t in items if x >= notes_from]
        rec = dict(min_tensile_ksi=None, min_yield_ksi=None, size_thickness=None)
        nums = [_num(t) for t in first]
        if table == "Table 1A":
            vals = [v for v in nums if v is not None]
            if len(vals) >= 2:
                rec["min_tensile_ksi"], rec["min_yield_ksi"] = vals[-2], vals[-1]
        else:
            vals = [v for v in nums if v is not None]
            if len(vals) >= 2:
                rec["min_tensile_ksi"], rec["min_yield_ksi"] = vals[-2], vals[-1]
            if first and re.search(r"\d\.\d", first[0]) and ("-" in first[0]):
                rec["size_thickness"] = first[0]
            elif first and not re.fullmatch(r"\d+(\.\d+)?", first[0]):
                rec["size_thickness"] = first[0]
        text = " ".join(v8).strip()
        rec["viii1_text"] = text or None
        if re.fullmatch(r"\d{2,4}", text):
            rec["viii1_limit_f"], rec["viii1_permitted"] = int(text), True
        elif text == "NP":
            rec["viii1_limit_f"], rec["viii1_permitted"] = None, False
        else:
            rec["viii1_limit_f"], rec["viii1_permitted"] = None, None      # not understood: never blocks
        note = " ".join(notes).strip()
        note = re.sub(r"\s+", " ", note).strip(" ,-")
        rec["notes_text"] = note or None
        out[n] = rec
    return out, None
