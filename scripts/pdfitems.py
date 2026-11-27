"""
pdfitems.py — positioned text from a PDF, standard library only.

    python scripts/pdfitems.py "data/statement.pdf"

Where pdftext.py just replays text in stream order, this reads the page tree,
decodes each font's own character encoding, and returns every run of text with
where it sits on the page — which is what a table needs (a bank statement's
withdrawal and deposit columns differ only by position).

It exists for the Local Bank statements import_statement.py reads. Those are
"encrypted" only in the sense of carrying usage restrictions: the password is
empty, so any PDF viewer opens them without asking. This does the same — it
derives the key from the empty password, and refuses a file that needs a real
one. It supports what those statements use (RC4 encryption, Flate streams,
object streams, simple fonts with /Differences, Type0 fonts with ToUnicode) and
is not a general PDF library.
"""
import hashlib
import re
import struct
import sys
import zlib
from collections import namedtuple
from pathlib import Path

Item = namedtuple("Item", "page cell x y text")   # cell = left edge of the clip box
MAX_STREAM = 20 * 1024 * 1024
PAD = bytes.fromhex("28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A")
STRING = rb"\((?:\\.|[^\\()])*\)"
REF = rb"(\d+)\s+\d+\s+R"
GLYPHS = {
    "space": " ", "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "period": ".",
    "comma": ",", "hyphen": "-", "minus": "-", "slash": "/", "backslash": "\\",
    "colon": ":", "semicolon": ";", "ampersand": "&", "parenleft": "(", "parenright": ")",
    "quotesingle": "'", "quotedbl": '"', "underscore": "_", "plus": "+", "asterisk": "*",
    "at": "@", "numbersign": "#", "percent": "%", "dollar": "$", "question": "?",
    "exclam": "!", "equal": "=", "less": "<", "greater": ">", "bracketleft": "[",
    "bracketright": "]", "ellipsis": "…", "bullet": "•", "endash": "–", "emdash": "—",
}


class PdfError(Exception):
    pass


def _rc4(key, data):
    s, j = list(range(256)), 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 255
        s[i], s[j] = s[j], s[i]
    out, i, j = bytearray(len(data)), 0, 0
    for n, b in enumerate(data):
        i = (i + 1) & 255
        j = (j + s[i]) & 255
        s[i], s[j] = s[j], s[i]
        out[n] = b ^ s[(s[i] + s[j]) & 255]
    return bytes(out)


def _literal(raw):
    """Bytes of a PDF literal string (without its parentheses)."""
    out, i = bytearray(), 0
    esc = {110: 10, 114: 13, 116: 9, 98: 8, 102: 12}
    while i < len(raw):
        c = raw[i]
        if c != 92 or i + 1 >= len(raw):
            out.append(c)
            i += 1
            continue
        m = re.match(rb"[0-7]{1,3}", raw[i + 1:i + 4])
        if m:
            out.append(int(m.group(), 8) & 255)
            i += 1 + len(m.group())
        else:
            out.append(esc.get(raw[i + 1], raw[i + 1]))
            i += 2
    return bytes(out)


def _file_key(data):
    """Encryption key for the EMPTY user password, or None if not encrypted."""
    m = re.search(rb"/Encrypt\s+" + REF, data)
    if not m:
        return None
    enc = re.search(rb"\b" + m.group(1) + rb"\s+\d+\s+obj(.*?)endobj", data, re.S)
    enc = enc.group(1) if enc else b""
    num = lambda name: int(re.search(rb"/" + name + rb"\s+(-?\d+)", enc).group(1))
    try:
        v, r, p = num(b"V"), num(b"R"), num(b"P")
        length = num(b"Length") // 8 if b"/Length" in enc else 5
        o = _literal(re.search(rb"/O\s*(" + STRING + rb")", enc, re.S).group(1)[1:-1])
        u = _literal(re.search(rb"/U\s*(" + STRING + rb")", enc, re.S).group(1)[1:-1])
        doc_id = bytes.fromhex(re.search(rb"/ID\s*\[\s*<([0-9a-fA-F]+)>", data).group(1).decode())
    except (AttributeError, ValueError):
        raise PdfError("unrecognised encryption dictionary")
    if v not in (1, 2) or r not in (2, 3):
        raise PdfError("encrypted with a scheme this reader doesn't support (not RC4)")
    key = hashlib.md5(PAD + o + struct.pack("<i", p) + doc_id).digest()
    if r == 3:
        for _ in range(50):
            key = hashlib.md5(key[:length]).digest()
    key = key[:length]
    if r == 2:
        check = _rc4(key, PAD)
    else:
        check = _rc4(key, hashlib.md5(PAD + doc_id).digest())
        for i in range(1, 20):
            check = _rc4(bytes(b ^ i for b in key), check)
    if check[:16] != u[:16]:
        raise PdfError("needs a password to open")
    return key


def _objects(data):
    """{object number: (dictionary/body bytes, decoded stream or None)}."""
    key = _file_key(data)
    objs = {}
    for m in re.finditer(rb"(\d+)\s+(\d+)\s+obj\b(.*?)endobj", data, re.S):
        num, gen, body = int(m.group(1)), int(m.group(2)), m.group(3)
        s = re.search(rb"stream\r?\n", body)
        if not s:
            objs[num] = (body, None)
            continue
        head, raw = body[:s.start()], body[s.end():body.rfind(b"endstream")]
        if key and b"/XRef" not in head:
            okey = hashlib.md5(key + struct.pack("<I", num)[:3] + struct.pack("<H", gen)).digest()
            raw = _rc4(okey[:min(len(key) + 5, 16)], raw)
        if b"FlateDecode" in head:
            try:
                raw = zlib.decompressobj().decompress(raw, MAX_STREAM)
            except zlib.error:
                raw = None
        objs[num] = (head, raw)
    # Objects packed inside object streams (fonts and pages usually live here).
    for head, stream in list(objs.values()):
        if stream is None or b"/ObjStm" not in head:
            continue
        first = int(re.search(rb"/First\s+(\d+)", head).group(1))
        index = [int(t) for t in stream[:first].split()]
        pairs = list(zip(index[::2], index[1::2]))
        for k, (num, off) in enumerate(pairs):
            end = pairs[k + 1][1] if k + 1 < len(pairs) else len(stream) - first
            objs.setdefault(num, (stream[first + off:first + end], None))
    return objs


def _deref(objs, value):
    """If `value` is an indirect reference, return the referenced body."""
    m = re.fullmatch(rb"\s*" + REF + rb"\s*", value)
    return objs.get(int(m.group(1)), (b"", None))[0] if m else value


def _entry(objs, body, name):
    """Value of /name in a dictionary body: a reference target, or the inline
    dictionary/array/name that follows it."""
    m = re.search(rb"/" + name + rb"\s*(" + REF + rb"|<<|\[|/[^\s/<>\[\]()]+)", body)
    if not m:
        return b""
    if m.group(1) in (b"<<", b"["):
        open_, close = (b"<<", b">>") if m.group(1) == b"<<" else (b"[", b"]")
        depth, i = 0, m.start(1)
        for t in re.finditer(re.escape(open_) + rb"|" + re.escape(close), body[i:]):
            depth += 1 if t.group() == open_ else -1
            if depth == 0:
                return body[i:i + t.end()]
        return body[i:]
    return _deref(objs, m.group(1))


def _decoder(objs, font):
    """A function turning this font's string bytes into text."""
    tu = re.search(rb"/ToUnicode\s+" + REF, font)
    if tu and objs.get(int(tu.group(1)), (0, None))[1]:
        cmap, table = objs[int(tu.group(1))][1], {}
        h = lambda b: int(b, 16)
        uni = lambda b: bytes.fromhex(b.decode()).decode("utf-16-be", "replace")
        for block in re.findall(rb"beginbfchar(.*?)endbfchar", cmap, re.S):
            for src, dst in re.findall(rb"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>", block):
                table[h(src)] = uni(dst)
        for block in re.findall(rb"beginbfrange(.*?)endbfrange", cmap, re.S):
            for lo, hi, dst in re.findall(
                    rb"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>", block):
                for k in range(h(hi) - h(lo) + 1):
                    table[h(lo) + k] = chr(h(dst) + k) if len(dst) <= 4 else uni(dst)
        wide = b"Identity" in font or b"/Type0" in font
        step = 2 if wide else 1
        return lambda raw: "".join(
            table.get(int.from_bytes(raw[i:i + step], "big"), "") for i in range(0, len(raw), step))
    table = {}
    enc = _entry(objs, font, b"Encoding")
    diff = re.search(rb"/Differences\s*\[(.*?)\]", enc, re.S)
    if diff:
        code = 0
        for tok in re.findall(rb"\d+|/[^\s/\[\]]+", diff.group(1)):
            if tok[:1] != b"/":
                code = int(tok)
                continue
            name = tok[1:].decode("latin-1")
            table[code] = name if len(name) == 1 else GLYPHS.get(name, "")
            code += 1
    return lambda raw: "".join(table.get(b) if b in table else bytes([b]).decode("cp1252", "replace")
                               for b in raw)


def _pages(objs):
    """Page dictionary bodies in reading order."""
    def walk(num, seen):
        body = objs.get(num, (b"", None))[0]
        if num in seen:
            return
        seen.add(num)
        if re.search(rb"/Type\s*/Pages\b", body):
            kids = re.search(rb"/Kids\s*\[(.*?)\]", body, re.S)
            for kid in re.findall(REF, kids.group(1) if kids else b""):
                yield from walk(int(kid), seen)
        elif re.search(rb"/Type\s*/Page\b", body):
            yield body
    roots = [n for n, (b, _) in objs.items()
             if re.search(rb"/Type\s*/Pages\b", b) and b"/Parent" not in b]
    seen = set()
    for root in roots:
        yield from walk(root, seen)


TOKEN = re.compile(
    STRING + rb"|<[0-9A-Fa-f\s]*>|\[|\]|/[^\s/<>\[\]()]+|[-+]?\d*\.?\d+|[A-Za-z'\"*]+", re.S)


def extract_items(path):
    """Every text run in the PDF as Item(page, cell, x, y, text), in page order."""
    data = Path(path).read_bytes()
    objs = _objects(data)
    items = []
    for pno, page in enumerate(_pages(objs)):
        fonts = {}
        res = _entry(objs, page, b"Resources")
        fdict = _entry(objs, res, b"Font")
        for name, num in re.findall(rb"/([^\s/<>\[\]()]+)\s+" + REF, fdict):
            fonts[name] = _decoder(objs, objs.get(int(num), (b"", None))[0])
        contents = re.search(rb"/Contents\s*(\[.*?\]|" + REF + rb")", page, re.S)
        stream = b"\n".join(objs[int(n)][1] or b"" for n in re.findall(REF, contents.group(1))
                            if int(n) in objs) if contents else b""
        decode = lambda raw: raw.decode("latin-1")
        stack, cell, x, y, lead, in_array = [], None, 0.0, 0.0, 0.0, None
        for m in TOKEN.finditer(stream):
            t = m.group()
            c = t[:1]
            if c == b"(":
                val = _literal(t[1:-1])
            elif c == b"<":
                val = bytes.fromhex(re.sub(rb"\s", b"", t[1:-1]).decode() or "")
            elif c == b"[":
                in_array = []
                continue
            elif c == b"]":
                stack.append(b"".join(v for v in in_array if isinstance(v, bytes)))
                in_array = None
                continue
            elif c == b"/":
                val = t
            elif c.isdigit() or c in b"-+.":
                val = float(t)
            else:                                   # an operator
                try:
                    if t == b"re":
                        cell = stack[-4]
                    elif t == b"BT":
                        x = y = 0.0
                    elif t == b"Tf":
                        decode = fonts.get(stack[-2][1:], lambda raw: raw.decode("latin-1"))
                    elif t in (b"Td", b"TD"):
                        x, y = x + stack[-2], y + stack[-1]
                        if t == b"TD":
                            lead = -stack[-1]
                    elif t == b"TL":
                        lead = stack[-1]
                    elif t == b"Tm":
                        x, y = stack[-2], stack[-1]
                    elif t in (b"T*", b"'", b'"'):
                        y -= lead
                    if t in (b"Tj", b"TJ", b"'", b'"') and isinstance(stack[-1], bytes):
                        text = decode(stack[-1])
                        if text:
                            items.append(Item(pno, cell, round(x, 2), round(y, 2), text))
                except (IndexError, TypeError):
                    pass
                stack = []
                continue
            (in_array if in_array is not None else stack).append(val)
    return items


def lines(items, tolerance=2.0):
    """Group items into visual rows: [(page, y, [items left to right])]."""
    rows = []
    for it in sorted(items, key=lambda i: (i.page, -i.y, i.x)):
        if rows and rows[-1][0] == it.page and abs(rows[-1][1] - it.y) <= tolerance:
            rows[-1][2].append(it)
        else:
            rows.append((it.page, it.y, [it]))
    return [(p, y, sorted(its, key=lambda i: i.x)) for p, y, its in rows]


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python scripts/pdfitems.py <file.pdf>")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for page, y, its in lines(extract_items(sys.argv[1])):
        print(f"p{page + 1} y={y:7.2f}  " + "  |  ".join(f"[{i.cell or 0:.0f}] {i.text}" for i in its))
