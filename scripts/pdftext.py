"""
pdftext.py — pull the text out of a PDF using nothing but the standard library.

    python scripts/pdftext.py "data/2026-08.pdf"

Written for the statements snapshot.py reads (Local Brokerage monthly
account statement, CDP account statement). Generic PDF readers keep mistaking
these for password-protected files; they aren't encrypted at all, their content
streams are just plain FlateDecode. This inflates every stream and replays the
text-showing operators, emitting a newline on each line-positioning operator so
table cells land one per line.

Not a general PDF parser: it ignores fonts, encodings beyond Latin-1, and layout
(no column reconstruction). That is enough to read a statement's numbers, which
is all this project needs. If a statement ever comes back empty here, it is
genuinely encrypted or uses a CID font, and it needs a real reader.
"""
import re
import sys
import zlib
from pathlib import Path

# Text-showing strings plus the operators that end a line. Strings are matched
# whole so an escaped paren inside one can't terminate it early.
TOKENS = re.compile(rb"\((?:\\.|[^\\()])*\)|TD|Td|T\*|ET", re.S)
MAX_STREAM = 20 * 1024 * 1024         # no statement page inflates past this
OCTAL = "01234567"                     # PDF octal escapes stop at 7
ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}
# Streams whose dictionary carries one of these are never page text: /Length1
# marks an embedded font program and /Subtype/Image a picture. Both inflate
# fine and both contain stray "Tj"/"BT" byte pairs, so without this they slip
# through as pages of mojibake.
NOT_TEXT = re.compile(rb"/Length1\b|/Subtype\s*/Image|/Type\s*/(?:Font|XObject)\b")


def _unescape(raw):
    """Resolve a PDF literal string's backslash escapes, octal included."""
    out, i = [], 0
    while i < len(raw):
        c = raw[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        nxt = raw[i + 1:i + 2]
        if nxt in OCTAL:                      # \053 -> '+'
            oct_digits = raw[i + 1:i + 4]
            while oct_digits and oct_digits[-1] not in OCTAL:
                oct_digits = oct_digits[:-1]
            out.append(chr(int(oct_digits, 8)))
            i += 1 + len(oct_digits)
        else:
            out.append(ESCAPES.get(nxt, nxt))
            i += 2
    return "".join(out)


def _looks_textual(chunk):
    """True if `chunk` could be an uncompressed content stream, not binary."""
    sample = chunk[:2048]
    if not sample:
        return False
    printable = sum(32 <= b < 127 or b in b"\r\n\t" for b in sample)
    return printable / len(sample) > 0.9


def extract_text(path):
    """Return the visible text of `path`, one line per positioned text run."""
    data = Path(path).read_bytes()
    if b"/Encrypt" in data:
        raise ValueError(f"{path} is encrypted — needs a password")
    out = []
    for m in re.finditer(rb"stream(?:\r\n|\r|\n)", data):
        start = m.end()
        end = data.find(b"endstream", start)
        chunk = data[start:end]
        head = data[max(0, m.start() - 400):m.start()]
        if NOT_TEXT.search(head[head.rfind(b"<<"):]):
            continue                           # a font program or an image
        try:
            # Capped, so a crafted "zip bomb" stream can't exhaust memory.
            chunk = zlib.decompressobj().decompress(chunk, MAX_STREAM)
        except zlib.error:
            # Either a stream stored raw, or — far more often — the word
            # "stream" sitting inside another stream's compressed bytes.
            # Keep it only if it reads as text, so a JPEG or an embedded font
            # can't spray binary over the output.
            if not _looks_textual(chunk):
                continue
        if b"Tj" not in chunk and b"TJ" not in chunk:
            continue                           # not a content stream
        for tok in TOKENS.finditer(chunk):
            t = tok.group()
            if t.startswith(b"("):
                out.append(_unescape(t[1:-1].decode("latin-1")))
            else:
                out.append("\n")
    return "".join(out)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python scripts/pdftext.py <file.pdf>")
    text = extract_text(sys.argv[1])
    if not text.strip():
        sys.exit("no text found — the PDF is likely scanned images or a CID font")
    print(text)
