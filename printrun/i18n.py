import ast
import os
import struct
from pathlib import Path


def _unescape_po_quoted(s: str) -> str:
    # PO strings use C-like escapes; Python's literal_eval handles them well.
    return ast.literal_eval(s)


def compile_mo_from_po(po_path: Path, mo_path: Path) -> None:
    """
    Compile a .po file into a .mo file (binary).
    This is a small, self-contained msgfmt implementation so users on Windows
    don't need external gettext tools to get translations working.
    """
    messages: dict[str, str] = {}

    def finish_entry(msgid: str | None, msgstr: str | None) -> None:
        if msgid is None or msgstr is None:
            return
        messages[msgid] = msgstr

    msgid: str | None = None
    msgstr: str | None = None
    in_msgid = False
    in_msgstr = False

    data = po_path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in data:
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("msgid "):
            finish_entry(msgid, msgstr)
            msgid = _unescape_po_quoted(line[5:].strip())
            msgstr = ""
            in_msgid, in_msgstr = True, False
            continue

        if line.startswith("msgstr "):
            msgstr = _unescape_po_quoted(line[6:].strip())
            in_msgid, in_msgstr = False, True
            continue

        if line.startswith('"'):
            frag = _unescape_po_quoted(line)
            if in_msgid and msgid is not None:
                msgid += frag
            elif in_msgstr and msgstr is not None:
                msgstr += frag
            continue

        # Ignore plural forms / contexts for now (still yields partial RU).
        # They can be added later by improving this compiler.

    finish_entry(msgid, msgstr)

    # The empty msgid ("") is the header. Ensure it's first in sorting.
    ids = sorted(messages.keys(), key=lambda k: (k != "", k))
    strs = [messages[i] for i in ids]

    # Build the binary .mo file.
    # Format: https://www.gnu.org/software/gettext/manual/html_node/MO-Files.html
    keystart = 7 * 4 + 16 * len(ids)
    id_bytes = [i.encode("utf-8") for i in ids]
    str_bytes = [s.encode("utf-8") for s in strs]

    offsets: list[tuple[int, int]] = []
    o = keystart
    for b in id_bytes:
        offsets.append((len(b), o))
        o += len(b) + 1

    trans_offsets: list[tuple[int, int]] = []
    for b in str_bytes:
        trans_offsets.append((len(b), o))
        o += len(b) + 1

    output = bytearray()
    # magic, version, nstrings, off_orig_tab, off_trans_tab, hash_size, hash_offset
    output += struct.pack("<Iiiiiii", 0x950412de, 0, len(ids), 7 * 4, 7 * 4 + 8 * len(ids), 0, 0)
    for l, off in offsets:
        output += struct.pack("<ii", l, off)
    for l, off in trans_offsets:
        output += struct.pack("<ii", l, off)
    for b in id_bytes:
        output += b + b"\x00"
    for b in str_bytes:
        output += b + b"\x00"

    mo_path.parent.mkdir(parents=True, exist_ok=True)
    mo_path.write_bytes(output)


def ensure_compiled_locale(domain: str, localedir: Path, language_tag: str | None) -> None:
    """
    Ensure .mo exists if .po exists for the requested language.
    language_tag examples: 'ru_RU', 'ru', 'de_DE', ...
    """
    if not language_tag:
        return

    # gettext will try language_tag then its base (ru_RU -> ru)
    candidates = [language_tag]
    if "_" in language_tag:
        candidates.append(language_tag.split("_", 1)[0])

    for lang in candidates:
        po = localedir / lang / "LC_MESSAGES" / f"{domain}.po"
        mo = localedir / lang / "LC_MESSAGES" / f"{domain}.mo"
        if not po.exists():
            continue
        if mo.exists() and mo.stat().st_mtime >= po.stat().st_mtime:
            continue
        try:
            compile_mo_from_po(po, mo)
        except Exception:
            # If compilation fails, silently fall back to English.
            continue
