"""React Server Components flight-payload decoder for cricbuzz.com pages.

A Next.js App Router page ships its server data as a sequence of
`<script>self.__next_f.push([1,"<chunk>"])</script>` calls. Concatenating the
decoded chunks gives the flight text: one row per line, `<hex id>:<payload>`.
Payload kinds that matter here:

    <id>:[...] / {...}      a JSON model row (component trees are arrays of
                            ["$", "<type>", key, {props}] elements)
    <id>:T<hexlen>,<text>   a text blob of exactly <hexlen> UTF-8 BYTES —
                            it is NOT newline-terminated, so a naive split
                            on "\\n" glues the next row onto it (the trap
                            that hid half the page data at first)
    <id>:I[...]             module imports (ignored)

`page_rows(html)` returns {id: parsed row}; `find_first`/`find_all` walk the
rows for the first dict carrying a given key, which is how every page's
data object is located (props of the client component that renders it).
Values that are references (`"$26"`) point at another row: `deref` resolves
the T-blob ones (player bios live there).
"""
import json
import re

_CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', re.S)
_ROW_ID_RE = re.compile(r"[0-9a-f]+")


def flight_text(html):
    """The concatenated, unescaped flight payload of one page."""
    parts = []
    for chunk in _CHUNK_RE.findall(html or ""):
        try:
            parts.append(json.loads('"' + chunk + '"'))
        except Exception:
            parts.append(chunk.encode("utf-8", "surrogatepass").decode("unicode_escape", errors="ignore"))
    return "".join(parts)


def parse_flight(text):
    """flight text -> {row id: parsed JSON | text blob | raw string}."""
    rows = {}
    data = (text or "").encode("utf-8")
    pos, n = 0, len(data)
    while pos < n:
        colon = data.find(b":", pos)
        if colon < 0:
            break
        rid = data[pos:colon].decode("ascii", "ignore")
        if not _ROW_ID_RE.fullmatch(rid):
            nl = data.find(b"\n", pos)
            if nl < 0:
                break
            pos = nl + 1
            continue
        pos = colon + 1
        if data[pos:pos + 1] == b"T":
            comma = data.find(b",", pos)
            try:
                length = int(data[pos + 1:comma], 16)
            except ValueError:
                length = 0
            rows[rid] = data[comma + 1:comma + 1 + length].decode("utf-8", "replace")
            pos = comma + 1 + length
            if data[pos:pos + 1] == b"\n":
                pos += 1
            continue
        nl = data.find(b"\n", pos)
        if nl < 0:
            nl = n
        body = data[pos:nl].decode("utf-8", "replace")
        pos = nl + 1
        if body[:1] in "[{":
            try:
                rows[rid] = json.loads(body)
                continue
            except ValueError:
                pass
        rows[rid] = body
    return rows


def page_rows(html):
    return parse_flight(flight_text(html))


def find_all(obj, key, limit=10):
    """Every dict (depth-first) that carries `key`, up to `limit`."""
    hits = []

    def walk(node):
        if len(hits) >= limit:
            return
        if isinstance(node, dict):
            if key in node:
                hits.append(node)
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(obj)
    return hits


def find_first(rows, key, *, where=None):
    """The first dict in any row that carries `key` (and satisfies `where`)."""
    for row in rows.values():
        if not isinstance(row, (dict, list)):
            continue
        for hit in find_all(row, key, limit=20):
            if where is None or where(hit):
                return hit
    return None


def deref(rows, value):
    """Resolve a "$<row id>" reference to its row (text blobs); other values
    pass through. "$undefined" becomes None."""
    if isinstance(value, str) and value.startswith("$"):
        if value == "$undefined":
            return None
        target = rows.get(value[1:])
        if isinstance(target, str):
            return target
        return None if value[1:2] in ("L", "S") else target
    return value


def undefined_to_none(obj):
    """Recursively turn the flight's "$undefined" placeholders into None."""
    if isinstance(obj, dict):
        return {k: undefined_to_none(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [undefined_to_none(v) for v in obj]
    if obj == "$undefined":
        return None
    return obj
