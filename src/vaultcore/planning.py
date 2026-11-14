"""Whole-vault planning calls: propose a folder structure, translate an instruction into rules."""
from __future__ import annotations

from collections import Counter

from .index import VaultIndex
from .llm import chat_json
from .pathutil import sanitize_folder
from .prompts import rules_system, rules_user, structure_system, structure_user
from .rules import RuleError, parse_rule
from .settings import Settings
from .taxonomy import Folder, Taxonomy


def vault_summary(index: VaultIndex, max_folders: int = 120, titles_per_folder: int = 8) -> str:
    groups = index.by_folder()
    ordered = sorted(groups.items(), key=lambda kv: -len(kv[1]))[:max_folders]
    lines = []
    for folder, notes in sorted(ordered, key=lambda kv: kv[0].lower()):
        tags: Counter[str] = Counter()
        for n in notes:
            tags.update(n.values.get("tags", []))
        titles = ", ".join(n.title for n in notes[:titles_per_folder])
        more = f" (+{len(notes) - titles_per_folder} more)" if len(notes) > titles_per_folder else ""
        tg = ", ".join(t for t, _ in tags.most_common(4))
        lines.append(f"- {folder or '(vault root)'} ({len(notes)} notes): {titles}{more}" + (f" | common tags: {tg}" if tg else ""))
    return "\n".join(lines)


async def propose_structure(settings: Settings, llm, index: VaultIndex, taxonomy: Taxonomy,
                            preferences: str = "", feedback: str = "") -> tuple[list[Folder], str]:
    current = taxonomy.prompt_lines() if len(taxonomy) else ""
    data, err = await chat_json(llm, structure_system(settings), structure_user(vault_summary(index), current, preferences, feedback),
                                max_tokens=4096)
    folders: list[Folder] = []
    seen = set()
    if isinstance(data, dict):
        for f in data.get("folders", []):
            if not isinstance(f, dict) or not f.get("path"):
                continue
            try:
                path = sanitize_folder(str(f["path"]))
            except ValueError:
                continue
            if path and path.lower() not in seen:
                seen.add(path.lower())
                folders.append(Folder(path, str(f.get("description", "")).strip()))
        note = str(data.get("notes_to_user", "") or "")
    else:
        note = err or "the model did not return valid JSON"
    return folders, note


async def instruction_to_rules(settings: Settings, llm, taxonomy: Taxonomy, index: VaultIndex,
                               text: str) -> tuple[list[str], str]:
    tags = [v for v, _ in index.vocab("tags", 60)]
    data, err = await chat_json(llm, rules_system(settings), rules_user(text, taxonomy.prompt_lines(), tags), max_tokens=1500)
    rules: list[str] = []
    expl = "" if data is not None else (err or "")
    if isinstance(data, dict):
        expl = str(data.get("explanation", "") or "")
        for r in data.get("rules", []) or []:
            r = str(r).strip()
            try:
                parse_rule(r)
            except RuleError:
                continue
            rules.append(r)
    return rules, expl
