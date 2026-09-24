"""Setting keys in a game config file, leaving everything else as it was.

A handful of the game's numbers are not in ``masters.db`` and not compiled into
a package either: they are read from a ``.ini`` in ``BrgGame/Config`` at
startup. Unreal calls that a config class - ``config(UIDebugEditParams)`` makes
``BrgUIDebugEditParams`` read ``BrgUIDebugEditParams.ini``, exactly as
``BrgGraphicsConfig`` reads ``BrgGraphicsConfig.ini``. When the file is not
there the class keeps its compiled defaults, so writing one supplies values the
game was always willing to take, and nothing it shipped is edited.

The rules this follows, because a config file usually belongs to somebody:

* Change only the keys asked for. Never reorder, never reformat, never
  normalise whitespace - a diff should show the edit and nothing else.
* Keep the file's own line endings and encoding. The per-class files are plain
  text with CRLF, but the ``SteamPCRelease-*`` ones are UTF-16 with a BOM, and
  reading one of those as UTF-8 turns every CR into LF and the game can no
  longer read its own config.
* A key may legitimately appear more than once in a section (UE3 repeats
  ``Bindings=`` and ``SeekFreePackage=``), so setting one replaces the first
  and drops the rest rather than assuming there was only ever one.

Nothing here writes to disk. ``build`` returns bytes and the asset runner puts
them down, so a config file goes through the same backup, rollback and restore
as any other game file the manager touches.
"""

from __future__ import annotations

import codecs
import re

# The per-class config files are plain text with Windows line endings, so that
# is what a file we create from nothing looks like.
DEFAULT_NEWLINE = "\r\n"

# UTF-8 last: it has no BOM to match, so it is the answer when nothing else is.
_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)


def sniff(raw: bytes) -> tuple[str, bytes]:
    """(encoding, byte order mark) for a config file's bytes.

    UTF-32-LE starts with the UTF-16-LE mark followed by two zero bytes, so the
    wider one has to be tried first or every UTF-32 file reads as UTF-16.
    """
    for mark, encoding in _BOMS:
        if raw.startswith(mark):
            return encoding, mark
    return "utf-8", b""


def decode(raw: bytes) -> str:
    """The text of a config file, with no newline translation at all."""
    encoding, mark = sniff(raw)
    return raw[len(mark):].decode(encoding, "surrogateescape")


def encode(text: str, encoding: str = "utf-8", mark: bytes = b"") -> bytes:
    return mark + text.encode(encoding, "surrogateescape")


def _newline(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    if "\n" in text:
        return "\n"
    return DEFAULT_NEWLINE


def _lines(text: str) -> list[str]:
    """Split keeping the line endings, so joining back is byte for byte."""
    return text.splitlines(True)


def _is_header(line: str, name: str | None = None) -> bool:
    stripped = line.strip()
    if not (stripped.startswith("[") and stripped.endswith("]")):
        return False
    return True if name is None else stripped[1:-1].strip().lower() == name.lower()


def section_span(text: str, section: str) -> tuple[int, int] | None:
    """(first, last+1) line numbers of a section's body, or None if absent."""
    lines = _lines(text)
    start = None
    for number, line in enumerate(lines):
        if _is_header(line, section):
            start = number + 1
            break
    if start is None:
        return None
    end = len(lines)
    for number in range(start, len(lines)):
        if _is_header(lines[number]):
            end = number
            break
    return start, end


def _key_pattern(key: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*{re.escape(key)}\s*=", re.IGNORECASE)


def get(text: str, section: str, key: str, default: str | None = None) -> str | None:
    """The first value for a key in a section, or ``default``."""
    span = section_span(text, section)
    if span is None:
        return default
    pattern = _key_pattern(key)
    for line in _lines(text)[span[0]:span[1]]:
        if pattern.match(line):
            return line.split("=", 1)[1].strip()
    return default


def set_key(text: str, section: str, key: str, value: str) -> str:
    """``key=value`` in ``section``, adding the section or the key if missing.

    A key already there is replaced where it stands, so the file keeps its own
    order. Later copies of the same key in that section are dropped: the game
    reads one value, and leaving a second behind would make what the file says
    depend on which one it happened to read.
    """
    newline = _newline(text)
    line = f"{key}={value}{newline}"
    span = section_span(text, section)

    if span is None:
        if text and not text.endswith(("\n", "\r")):
            text += newline
        return f"{text}[{section}]{newline}{line}"

    start, end = span
    pattern = _key_pattern(key)
    out: list[str] = []
    placed = False
    for number, existing in enumerate(_lines(text)):
        if start <= number < end and pattern.match(existing):
            if not placed:
                out.append(line)
                placed = True
            continue
        out.append(existing)
    if not placed:
        # After the section's last real line rather than after the blank lines
        # that separate it from the next header, so an added key does not end up
        # on the far side of a gap from the ones it belongs with.
        at = end
        while at > start and not out[at - 1].strip():
            at -= 1
        # A last line with no line ending of its own - the file did not end in
        # one - would otherwise have the new key run straight on from it.
        if at and not out[at - 1].endswith(("\n", "\r")):
            out[at - 1] += newline
        out.insert(at, line)
    return "".join(out)


def build(base: bytes | None, entries) -> bytes:
    """A config file's bytes with every (section, key, value) laid over it.

    ``base`` is the file as it was, or None when there is none to start from -
    in which case a plain UTF-8 file with Windows line endings is written,
    matching the ones the game keeps beside it.
    """
    if base is None:
        encoding, mark, text = "utf-8", b"", ""
    else:
        encoding, mark = sniff(base)
        text = decode(base)
    for section, key, value in entries:
        text = set_key(text, section, key, str(value))
    return encode(text, encoding, mark)
