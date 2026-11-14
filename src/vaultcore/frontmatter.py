"""Byte-preserving header (frontmatter) engine.

Why this is NOT a YAML parser: the target format (``status: #Idea``,
``tags: [[A]], [[B]]``) is not valid YAML — a bare ``#`` after a colon starts a
comment and ``[[A]], [[B]]`` is not a flow sequence. So headers are handled as plain
``key: value`` lines (with indented / ``- item`` continuation lines belonging to the
previous key).

Guarantees (covered by tests):
  * ``render(parse(text)) == text`` — nothing is normalised on a round trip.
  * Writing fields never changes the note body (not even leading blank lines).
  * Existing header lines are only touched for keys explicitly passed in ``overwrite``
    (or keys whose value is empty); everything else is preserved byte-for-byte.
  * CRLF / LF and a UTF-8 BOM are preserved; new lines use the file's own newline style.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Iterable

from .settings import FieldSpec

_LINE_RE = re.compile(r"[^\n]*\n|[^\n]+")
_KEY_RE = re.compile(r"^([^\s#:\-\[\]{}\"'&*!|>%@`][^:]*?)\s*:(.*)$")
_WIKI_RE = re.compile(r"\[\[(.+?)\]\]")


@dataclass
class Entry:
    key: str | None                 # None = stray lines that are not part of any key
    lines: list[str]                # raw lines incl. line endings

    @property
    def first_value(self) -> str:
        if self.key is None:
            return ""
        m = _KEY_RE.match(self.lines[0].rstrip("\r\n"))
        return m.group(2).strip() if m else ""

    @property
    def continuation(self) -> list[str]:
        return [l.rstrip("\r\n") for l in self.lines[1:] if l.strip()]

    @property
    def is_empty(self) -> bool:
        return self.first_value == "" and not self.continuation

    def values(self) -> list[str]:
        """All values as a clean list (wikilinks/hashtags/quotes/brackets stripped)."""
        items: list[str] = []
        fv = self.first_value
        if fv:
            items.extend(to_list(fv))
        for c in self.continuation:
            c = c.strip()
            if c.startswith("-"):
                items.extend(to_list(c[1:].strip()))
            elif c.startswith("#"):
                continue  # YAML comment inside a block
            else:
                items.extend(to_list(c))
        return _dedupe(items)


@dataclass
class Parsed:
    bom: str
    open_line: str
    inner: list[str]
    close_line: str
    body: str
    nl: str
    has_fm: bool
    original_body_prefix: str = ""  # for no-frontmatter files: whole text (without BOM) is the body
    lead: str = ""                  # blank lines before the opening `---` (Obsidian ignores such a header; we still recognise it)

    @property
    def entries(self) -> list[Entry]:
        ents: list[Entry] = []
        cur: Entry | None = None
        for line in self.inner:
            raw = line.rstrip("\r\n")
            m = _KEY_RE.match(raw) if raw and not raw[0].isspace() else None
            if m:
                cur = Entry(m.group(1).strip(), [line])
                ents.append(cur)
            elif cur is not None:
                cur.lines.append(line)
            else:
                cur = Entry(None, [line])
                ents.append(cur)
        return ents

    def get(self, key: str) -> Entry | None:
        kl = key.lower()
        for e in self.entries:
            if e.key is not None and e.key.lower() == kl:
                return e
        return None


# --------------------------------------------------------------------------- parsing

def detect_newline(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def parse(text: str) -> Parsed:
    bom = "﻿" if text.startswith("﻿") else ""
    s = text[len(bom):]
    nl = detect_newline(s)
    lines = _LINE_RE.findall(s)
    k = 0
    while k < len(lines) and k < 5 and not lines[k].strip():
        k += 1
    if k < len(lines) and lines[k].rstrip("\r\n").rstrip() == "---":
        for i in range(k + 1, len(lines)):
            if lines[i].rstrip("\r\n").rstrip() in ("---", "..."):
                return Parsed(bom, lines[k], lines[k + 1:i], lines[i], "".join(lines[i + 1:]), nl, True, lead="".join(lines[:k]))
    return Parsed(bom, "", [], "", s, nl, False)


def render(p: Parsed) -> str:
    if not p.has_fm:
        return p.bom + p.body
    return p.bom + p.lead + p.open_line + "".join(p.inner) + p.close_line + p.body


def body_of(text: str) -> str:
    return parse(text).body


def title_and_snippet(text: str, limit: int = 300) -> str:
    return body_of(text)[:limit]


# --------------------------------------------------------------------------- value helpers

def _clean(v: str) -> str:
    v = v.strip()
    m = _WIKI_RE.fullmatch(v.strip("\"'"))
    if m:
        v = m.group(1)
    v = v.strip().strip("\"'").strip()
    v = v.lstrip("#").strip()
    if v.startswith("[[") and v.endswith("]]"):
        v = v[2:-2]
    return v.strip()


def _dedupe(items: Iterable[str]) -> list[str]:
    seen, out = set(), []
    for it in items:
        it = it.strip()
        if it and it.lower() not in seen:
            seen.add(it.lower())
            out.append(it)
    return out


def to_list(raw: str) -> list[str]:
    """Parse ``[[A]], [[B]]`` / ``[a, b]`` / ``a, b`` / ``#a #b`` / ``"[[A]]"`` into clean items."""
    raw = (raw or "").strip()
    if not raw:
        return []
    wl = _WIKI_RE.findall(raw)
    if wl:
        # keep an alias-free target: [[Note|alias]] -> Note
        return _dedupe(w.split("|")[0] for w in wl)
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    # hashtags separated by spaces: "#a #b"
    if "," not in raw and re.fullmatch(r"(#\S+)(\s+#\S+)*", raw):
        return _dedupe(t.lstrip("#") for t in raw.split())
    return _dedupe(_clean(p) for p in raw.split(","))


def normalize_value(spec: FieldSpec | None, value) -> list[str] | str:
    """Clean a proposed/edited value for `spec`: list kinds -> list[str], others -> str."""
    if spec is not None and spec.is_list:
        if isinstance(value, str):
            items = to_list(value)
        else:
            items = _dedupe(_clean(str(v)) for v in (value or []))
        return items[: spec.max_items] if spec.max_items else items
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value)
    s = _clean(str(value if value is not None else "")) if (spec is None or spec.kind != "date") else str(value or "").strip()
    return s


def format_line(spec: FieldSpec | None, key: str, value) -> str:
    """One header line (no newline) for `key` according to `spec` (None = plain text)."""
    kind = spec.kind if spec else "text"
    v = normalize_value(spec, value)
    if isinstance(v, list):
        if kind == "wikilink_list":
            text = ", ".join(f"[[{x}]]" for x in v)
        elif kind == "list":
            text = "[" + ", ".join(v) + "]"
        else:
            text = ", ".join(v)
    else:
        if kind == "wikilink":
            text = f"[[{v}]]" if v else ""
            if text and spec and spec.quote:
                text = f'"{text}"'
        elif kind == "hashtag":
            text = "#" + re.sub(r"\s+", "-", v) if v else ""
        elif kind == "date":
            text = v
        else:
            text = _yaml_safe(v)
    return f"{key}: {text}".rstrip()


def _yaml_safe(v: str) -> str:
    if v and (re.match(r"^[\[\]{}&*!|>'\"%@`#\-?]", v) or re.search(r":\s|\s#", v)):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return v


def _spec_for(schema: list[FieldSpec], key: str) -> FieldSpec | None:
    for s in schema:
        if s.key.lower() == key.lower():
            return s
    return None


# --------------------------------------------------------------------------- reading a header

def header_values(text: str, schema: list[FieldSpec]) -> dict[str, list[str]]:
    """key -> clean list of values (single-valued fields give a one-item list)."""
    p = parse(text)
    return {e.key: e.values() for e in p.entries if e.key}


def missing_keys(text: str, schema: list[FieldSpec]) -> list[str]:
    """Required, auto-fillable schema keys that are absent or empty in this note."""
    p = parse(text)
    out = []
    for s in schema:
        if not s.required or s.source == "none":
            continue
        e = p.get(s.key) if p.has_fm else None
        if e is None or e.is_empty:
            out.append(s.key)
    return out


# --------------------------------------------------------------------------- writing a header

def apply_fields(text: str, schema: list[FieldSpec], fields: dict, overwrite: Iterable[str] = ()) -> str:
    """Return `text` with `fields` written into its header.

    * a key that is absent (or has an empty value) is added/filled
    * a key that already has a value is only replaced if it is in `overwrite`
    * the body is returned byte-identical
    """
    overwrite_l = {k.lower() for k in overwrite}
    p = parse(text)
    nl = p.nl
    entries = p.entries if p.has_fm else []
    order = [s.key for s in schema if s.key in fields] + [k for k in fields if _spec_for(schema, k) is None]

    for key in order:
        spec = _spec_for(schema, key)
        new_line = format_line(spec, key, fields[key]) + nl
        e = next((x for x in entries if x.key and x.key.lower() == key.lower()), None)
        if e is None:
            entries.append(Entry(key, [new_line]))
        elif key.lower() in overwrite_l or e.is_empty:
            if _same_value(e, spec, fields[key]):
                continue
            e.lines = [new_line]
        # else: keep the existing value untouched

    inner = []
    for e in entries:
        for l in e.lines:
            inner.append(l)
    if inner and not inner[-1].endswith("\n"):
        inner[-1] += nl
    if p.has_fm:
        out = Parsed(p.bom, p.open_line, inner, p.close_line, p.body, nl, True)   # lead dropped: header must start on line 1
    else:
        if not inner:
            return text
        out = Parsed(p.bom, "---" + nl, inner, "---" + nl, p.body, nl, True)
    return render(out)


def _same_value(entry: Entry, spec: FieldSpec | None, value) -> bool:
    new = normalize_value(spec, value)
    old = entry.values()
    if isinstance(new, list):
        return old == new                       # exact: a case-only change (llm -> LLM) is a real change
    return (old[0] if old else "") == new and len(old) <= 1


def header_diff(old_text: str, new_text: str) -> list[tuple[str, str]]:
    """Diff of the header region only: [(' ' | '+' | '-', line)]."""
    def head(t: str) -> list[str]:
        p = parse(t)
        if not p.has_fm:
            return []
        return [p.open_line, *p.inner, p.close_line]

    a = [l.rstrip("\r\n") for l in head(old_text)]
    b = [l.rstrip("\r\n") for l in head(new_text)]
    out: list[tuple[str, str]] = []
    for line in difflib.ndiff(a, b):
        tag = line[:2]
        if tag == "? ":
            continue
        out.append((tag[0], line[2:]))
    return out
