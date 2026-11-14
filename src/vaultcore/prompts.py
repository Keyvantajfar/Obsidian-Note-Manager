"""Prompt construction. The *persona/guideline* text is user-editable (Settings → Prompts);
the *output-format* sections are generated from the header schema and are not editable,
so changing the schema can never desync the parser."""
from __future__ import annotations

from .rules import __doc__ as RULES_DOC
from .settings import FieldSpec, Settings

PROMPT_VERSION = "3"

DEFAULT_PROMPTS: dict[str, str] = {
    "analyze_base": (
        "You are a meticulous personal-knowledge-management assistant helping a user organize their "
        "Obsidian vault. You are given the raw Markdown of one or more notes, plus vocabulary already used "
        "elsewhere in the vault so you stay consistent with the user's existing system.\n"
        "Do NOT summarize, rewrite or comment on the notes themselves. Base every choice strictly on the "
        "note's actual content. Prefer reusing an existing value from the vocabulary when it genuinely fits "
        "instead of inventing a near-duplicate."
    ),
    "structure_base": (
        "You are a personal-knowledge-management consultant. You are given a summary of a user's current "
        "Obsidian vault: its folders, how many notes each holds, and sample note titles and common tags. "
        "Propose a clean, logical folder hierarchy (top-level folders, plus nested subfolders where useful) "
        "that organizes this vault well long-term. Aim for roughly 4-12 top-level folders. You may reuse good "
        "existing folder names. Give every folder a one-sentence description of what belongs in it — the "
        "description is used later to decide where each note goes, so make it concrete."
    ),
    "rules_base": (
        "You translate a user's plain-language instruction about organizing an Obsidian vault into precise "
        "rules in a small rule language. Only output rules that capture the instruction faithfully; if the "
        "instruction cannot be expressed as rules, output an empty list."
    ),
}


def prompt_text(settings: Settings, key: str) -> str:
    return (settings.prompts.get(key) or "").strip() or DEFAULT_PROMPTS[key]


# --------------------------------------------------------------------------- analyze (tag + route)

def field_schema_lines(fields: list[FieldSpec]) -> str:
    lines = []
    for f in fields:
        if f.is_list:
            ty = f'["...", ...] (up to {f.max_items} items)'
        else:
            ty = '"..."'
        ins = (f.instruction or f.title).strip()
        allowed = f" Allowed values: {', '.join(f.allowed)}." if f.allowed else ""
        lines.append(f'  - "{f.key}": {ty} — {ins}{allowed}')
    return "\n".join(lines)


def analyze_system(settings: Settings, want_fields: list[FieldSpec], want_route: bool, frozen: bool) -> str:
    parts = [prompt_text(settings, "analyze_base"), ""]
    shape = []
    if want_fields:
        shape.append('"fields": {' + ", ".join(f'"{f.key}": ...' for f in want_fields) + "}")
    if want_route:
        shape.append('"folder": "<one of the listed folder paths exactly, or empty string>"')
        shape.append('"new_folder": "<only if no listed folder fits: a suggested new folder path, else empty string>"')
    conf = []
    if want_fields:
        conf.append('"fields": <0..1>')
    if want_route:
        conf.append('"folder": <0..1>')
    shape.append('"confidence": {' + ", ".join(conf) + "}")
    shape.append('"reason": "<one short sentence>"')
    parts.append("Respond with STRICT JSON ONLY (no markdown fences, no commentary):")
    parts.append('{"results": [ {"id": "<note id as given>", ' + ", ".join(shape) + "} , ... ]}")
    parts.append("Return exactly one result object per note, using the note's id.")
    if want_fields:
        parts += ["", "Fields to fill:", field_schema_lines(want_fields)]
    if want_route:
        parts += ["", "Folder choice:"]
        if frozen:
            parts.append("  - The folder list is the user's FINAL structure. Always pick the best existing folder. "
                         "Only if NO listed folder is a reasonable fit, leave \"folder\" empty and put a suggested "
                         "new sub-folder path (under an existing folder) in \"new_folder\".")
        else:
            parts.append("  - Pick the single best folder from the list. If none fits well, leave \"folder\" empty "
                         "and suggest a new folder path in \"new_folder\".")
        parts.append("  - Notes linked to / from a note, and its sibling notes, are strong hints about where it belongs.")
    parts += ["", "\"confidence\" is your honest 0-1 estimate that your answer is right (0.5 = coin flip). "
                  "Be conservative; give low values when the note is short, vague or ambiguous."]
    return "\n".join(parts)


def analyze_user(settings: Settings, items: list[dict], want_fields: list[FieldSpec], want_route: bool,
                 folders_text: str, vocab: dict[str, list[str]], instructions: list[str]) -> str:
    """items: [{"id", "title", "text", "context": {...}}]"""
    out: list[str] = []
    if want_fields:
        vl = []
        for f in want_fields:
            vals = vocab.get(f.key) or []
            if vals:
                vl.append(f"- {f.key}: " + ", ".join(vals))
        out.append("VAULT VOCABULARY (most used first):\n" + ("\n".join(vl) if vl else "(none yet)"))
    if want_route:
        out.append("FOLDERS:\n" + (folders_text or "(none)"))
    if instructions:
        out.append("USER INSTRUCTIONS (follow exactly; they override your own judgment):\n" +
                   "\n".join(f"- {t}" for t in instructions))
    for it in items:
        ctx = it.get("context") or {}
        head = f"NOTE [id={it['id']}] title: {it['title']}"
        lines = [head]
        if want_route:
            if ctx.get("folder") is not None:
                lines.append(f"current folder: {ctx['folder'] or '(vault root)'}")
            if ctx.get("siblings"):
                lines.append("sibling notes: " + "; ".join(ctx["siblings"]))
            if ctx.get("links"):
                lines.append("linked notes (title → folder): " + "; ".join(ctx["links"]))
        lines.append("<<<")
        lines.append(it["text"])
        lines.append(">>>")
        out.append("\n".join(lines))
    out.append("Respond with the JSON object now.")
    return "\n\n".join(out)


# --------------------------------------------------------------------------- structure proposal

def structure_system(settings: Settings) -> str:
    return (prompt_text(settings, "structure_base") + "\n\nRespond with STRICT JSON ONLY:\n"
            '{"folders": [{"path": "<folder path, e.g. \'1_Zettelkasten\' or \'2_University/Calculus\'>", '
            '"description": "<one concrete sentence: what belongs here>"}], '
            '"notes_to_user": "<optional 1-3 sentence rationale>"}')


def structure_user(summary: str, current_plan: str, preferences: str, feedback: str) -> str:
    parts = ["CURRENT VAULT:\n" + summary]
    if current_plan:
        parts.append("CURRENT PLAN (revise this rather than starting over):\n" + current_plan)
    if preferences:
        parts.append("USER PREFERENCES (hard constraints, follow exactly where they apply):\n" + preferences)
    if feedback:
        parts.append("The user reviewed the previous proposal and said (revise accordingly):\n" + feedback)
    parts.append("Respond with the JSON object now.")
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- instruction -> rules

def rules_system(settings: Settings) -> str:
    return (prompt_text(settings, "rules_base") + "\n\nRULE LANGUAGE:\n" + (RULES_DOC or "") +
            "\n\nRespond with STRICT JSON ONLY: "
            '{"rules": ["<one rule per string>", ...], "explanation": "<one short sentence>"}\n'
            "Use only folder paths from the FOLDERS list for move: actions.")


def rules_user(instruction: str, folders_text: str, tag_vocab: list[str]) -> str:
    return (f"FOLDERS:\n{folders_text or '(none)'}\n\nEXISTING TAGS: {', '.join(tag_vocab[:60]) or '(none)'}\n\n"
            f"INSTRUCTION:\n{instruction}\n\nRespond with the JSON object now.")
