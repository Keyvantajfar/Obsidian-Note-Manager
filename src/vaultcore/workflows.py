"""Workspaces ("workflows"): the same window showing only the tabs one job needs.

UI-independent copy of the table the TUI home screen uses, so a web/desktop UI can share it.
"""
from __future__ import annotations

# id -> title, description, tabs in order, needs a model?, landing tab
WORKFLOWS: dict[str, dict] = {
    "all": {
        "title": "ALL", "needs_llm": True, "landing": "explorer",
        "description": "Everything in one window: headers, folders, dates, review and undo.",
        "tabs": ["explorer", "tagger", "organizer", "dates", "stage", "jobs", "tags", "rules", "settings"],
    },
    "header": {
        "title": "HEADER", "needs_llm": True, "landing": "tagger",
        "description": "Tag notes and fix headers: parent, status, tags, date. No folder moves.",
        "tabs": ["explorer", "tagger", "dates", "stage", "jobs", "tags", "rules", "settings"],
    },
    "organizer": {
        "title": "ORGANIZER", "needs_llm": True, "landing": "organizer",
        "description": "Plan your folders, route notes into them, review the moves. Headers stay untouched.",
        "tabs": ["explorer", "organizer", "stage", "jobs", "rules", "settings"],
    },
    "dates": {
        "title": "DATE FIXER", "needs_llm": False, "landing": "dates",
        "description": "Fix missing, broken or copy-time dates from file timestamps. Offline: no model, no API calls.",
        "tabs": ["explorer", "dates", "stage", "settings"],
    },
}

TAB_LABELS = {
    "explorer": "Explorer", "tagger": "Tagger", "organizer": "Organizer", "dates": "Dates",
    "stage": "Stage", "jobs": "Jobs", "tags": "Tags", "rules": "Rules", "settings": "Settings",
}


def apply_workflow(session, wid: str) -> None:
    """Switch the core's AI switches to match a workflow (what the TUI does when you pick a tile)."""
    if wid not in WORKFLOWS:
        raise KeyError(wid)
    session.offline = not WORKFLOWS[wid]["needs_llm"]
    session.header_enabled = wid != "organizer"
    session.route_enabled = wid in ("all", "organizer")
