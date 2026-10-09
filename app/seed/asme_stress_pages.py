"""Read the allowable-stress pages of ASME II-D Table 1A / 1B with the temperatures taken from the header of
EACH page (Table 1B has columns 350, 450, 550 and 1700-1800 F that Table 1A does not), and every value matched
to its column by x position, so a blank cell can never shift the values that follow it.

parse_stress_page(page) -> ({line_no: {temp_f: (value_float, decimals)}}, header_temps)
"""
import re

LINE_X_MAX = 92
REACH = 7.0
BLANK = {"-", "…", "...", ""}


def _norm(t):
    return t.replace("�", "-").replace("–", "-").strip()


def parse_stress_page(page):
    words = [((w[0] + w[2]) / 2, (w[1] + w[3]) / 2, w[0], _norm(w[4])) for w in page.get_text("words")]

    # header row: the "No." row with the temperatures
    hdr_y = None
    for cx, cy, x0, t in words:
        if t == "No." and cx < LINE_X_MAX:
            temps = [(c, int(tt)) for c, y, _, tt in words if abs(y - cy) < 3 and re.fullmatch(r"\d{3,4}", tt)]
            if len(temps) >= 5:
                hdr_y = cy
                break
    if hdr_y is None:
        return {}, []
    cols = sorted((c, v) for c, v in temps)
    gap = min(b[0] - a[0] for a, b in zip(cols, cols[1:])) if len(cols) > 1 else 30
    first_x = cols[0][0] - gap

    anchors = sorted((y, int(t)) for x, y, _, t in words
                     if x < LINE_X_MAX and t.isdigit() and 1 <= int(t) <= 45 and y > hdr_y)
    rows = {}
    for cx, cy, x0, t in words:
        if cy <= hdr_y or cx < first_x or t in BLANK:
            continue
        if not re.fullmatch(r"\d+(\.\d+)?", t):
            continue
        near = min(anchors, key=lambda a: abs(a[0] - cy), default=None)
        if near is None or abs(near[0] - cy) > REACH:
            continue
        col_x, temp = min(cols, key=lambda c: abs(c[0] - cx))
        if abs(col_x - cx) > gap * 0.6:
            continue
        rec = rows.setdefault(near[1], {})
        dec = len(t.split(".")[1]) if "." in t else 0
        if temp in rec and rec[temp][0] != float(t):
            rec[temp] = (None, dec)          # two different numbers claim one column: never guess
        else:
            rec[temp] = (float(t), dec)
    return rows, [v for _, v in cols]
