"""Vocabulary tools for header values (tags, MOCs, statuses...): usage counts and
near-duplicate detection (``LLM`` vs ``LLMs`` vs ``llm``)."""
from __future__ import annotations

import difflib
import re
from collections import Counter

from .index import VaultIndex


def usage(index: VaultIndex, key: str) -> list[tuple[str, int]]:
    """All distinct values of header field `key` with counts, most used first (case-insensitive merge)."""
    c: Counter[str] = Counter()
    variants: dict[str, Counter[str]] = {}
    for n in index.notes.values():
        for k, vals in n.values.items():
            if k.lower() != key.lower():
                continue
            for v in vals:
                c[v.lower()] += 1
                variants.setdefault(v.lower(), Counter())[v] += 1
    canon = {k: v.most_common(1)[0][0] for k, v in variants.items()}      # most common casing wins
    return [(canon[k], n) for k, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))]


def _norm(v: str) -> str:
    s = re.sub(r"[^a-z0-9]", "", v.lower())
    if len(s) > 3 and s.endswith("s"):
        s = s[:-1]
    return s


def near_duplicate_groups(values: list[tuple[str, int]], cutoff: float = 0.9) -> list[list[str]]:
    """Groups of values that probably mean the same thing."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    names = [v for v, _ in values][:3000]
    by_norm: dict[str, list[str]] = {}
    for v in names:
        by_norm.setdefault(_norm(v), []).append(v)
    for grp in by_norm.values():
        for other in grp[1:]:
            union(grp[0], other)
    norms = list(by_norm)
    for i, a in enumerate(norms):
        for b in norms[i + 1:]:
            if abs(len(a) - len(b)) > 2 or len(a) < 5 or a[0] != b[0]:
                continue
            if difflib.SequenceMatcher(None, a, b).ratio() >= cutoff:
                union(by_norm[a][0], by_norm[b][0])
    groups: dict[str, list[str]] = {}
    for v in names:
        groups.setdefault(find(v), []).append(v)
    counts = dict(values)
    out = [sorted(g, key=lambda v: -counts.get(v, 0)) for g in groups.values() if len(g) > 1]
    out.sort(key=lambda g: -sum(counts.get(v, 0) for v in g))
    return out
