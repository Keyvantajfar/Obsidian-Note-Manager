"""Settings: nothing is hard-coded — everything here is a default that the user can change.

Two files:
  * global   ``<config dir>/vaultmgr/profiles.json``  — LLM profiles (may hold API keys, chmod 600)
  * per vault ``<vault>/.vaultmgr/settings.json``      — everything else (safe to sync/commit, no secrets)
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, fields, asdict
from pathlib import Path
from typing import Any

from .store import read_json, write_json

STATE_DIR = ".vaultmgr"

FIELD_KINDS = ["text", "wikilink", "hashtag", "wikilink_list", "list", "csv", "date"]
FIELD_SOURCES = ["llm", "created", "modified", "static", "none"]
AUTO_LEVELS = ["manual", "assist", "auto"]
COLLISION_POLICIES = ["suffix", "skip"]
DATE_SOURCES = ["earliest", "mtime", "birth_or_mtime", "name_or_earliest"]


# --------------------------------------------------------------------------- header schema

@dataclass
class FieldSpec:
    """One header (frontmatter) field.

    kind: how the value is written.
        text           key: value
        wikilink       key: [[value]]            (``quote`` wraps in "..."): key: "[[value]]"
        hashtag        key: #Value
        wikilink_list  key: [[A]], [[B]]
        list           key: [A, B]
        csv            key: A, B
        date           key: <formatted date>
    source: where the value comes from.
        llm      the model proposes it (``instruction`` is shown to the model)
        created  note creation time (never guessed by the model)
        modified note modification time
        static   always ``default``
        none     never filled automatically (still editable by hand)
    """
    key: str
    kind: str = "text"
    label: str = ""
    source: str = "llm"
    instruction: str = ""
    required: bool = True
    quote: bool = False
    default: str = ""
    max_items: int = 10
    min_items: int = 1
    allowed: list[str] = field(default_factory=list)
    date_format: str = "%Y-%m-%dT%H:%M"

    @property
    def is_list(self) -> bool:
        return self.kind in ("wikilink_list", "list", "csv")

    @property
    def title(self) -> str:
        return self.label or self.key


def default_schema() -> list[FieldSpec]:
    """Defaults reproduce the original tool's four fields; edit/replace freely."""
    return [
        FieldSpec(
            key="parent", kind="wikilink", quote=True, label="Parent MOC",
            instruction="The single best-fit MOC (Map of Content) name for this note, e.g. "
                        "'Fleeting MOC' or 'Deep Learning MOC'. Reuse an existing MOC from the vocabulary when it fits.",
            default="Fleeting MOC",
        ),
        FieldSpec(
            key="status", kind="hashtag", label="Status",
            instruction="One short word for the note's maturity, e.g. Idea, Draft, Fleeting, Permanent, Reference, Complete.",
            default="Fleeting",
        ),
        FieldSpec(
            key="tags", kind="wikilink_list", label="Tags", max_items=10, min_items=3,
            instruction="5 to 10 short, high-signal topical tags (1-3 word Title Case noun phrases). "
                        "Reuse existing tags from the vocabulary when they genuinely fit. No generic filler like 'Notes' or 'Misc'.",
            default="Fleeting",
        ),
        FieldSpec(key="date", kind="date", label="Date", source="created", date_format="%Y-%m-%dT%H:%M"),
    ]


# --------------------------------------------------------------------------- LLM profiles

@dataclass
class LLMProfile:
    name: str = "llama.cpp (local)"
    base_url: str = "http://localhost:8080/v1"
    api_key: str = ""
    api_key_env: str = ""          # name of an env var holding the key (preferred over storing it)
    model: str = ""
    temperature: float = 0.2
    max_tokens: int = 2048
    timeout: float = 300.0
    max_retries: int = 3
    json_mode: str = "auto"        # auto | on | off  (response_format=json_object)
    reasoning: str = "auto"        # auto | off | on: "off" always asks the server to skip thinking; "auto" does so after a
                                   # reply came back empty because a reasoning model spent its whole token budget thinking
    extra_body: dict[str, Any] = field(default_factory=dict)      # e.g. {"chat_template_kwargs": {"enable_thinking": false}}
    extra_headers: dict[str, str] = field(default_factory=dict)

    def resolved_key(self) -> str:
        if self.api_key_env:
            return os.environ.get(self.api_key_env, "") or self.api_key
        return self.api_key


PRESETS: dict[str, dict[str, str]] = {
    "llama.cpp server": {"base_url": "http://localhost:8080/v1"},
    "Ollama": {"base_url": "http://localhost:11434/v1"},
    "LM Studio": {"base_url": "http://localhost:1234/v1"},
    "vLLM": {"base_url": "http://localhost:8000/v1"},
    "OpenAI": {"base_url": "https://api.openai.com/v1"},
    "OpenRouter": {"base_url": "https://openrouter.ai/api/v1"},
    "Custom (OpenAI-compatible)": {"base_url": ""},
}


def config_home() -> Path:
    env = os.environ.get("VAULTMGR_HOME")
    if env:
        return Path(env)
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "vaultmgr"


@dataclass
class Profiles:
    profiles: list[LLMProfile] = field(default_factory=lambda: [LLMProfile()])
    active: str = ""
    recent_vaults: list[str] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return config_home() / "profiles.json"

    def get_active(self) -> LLMProfile:
        for p in self.profiles:
            if p.name == self.active:
                return p
        return self.profiles[0] if self.profiles else LLMProfile()

    def upsert(self, profile: LLMProfile) -> None:
        for i, p in enumerate(self.profiles):
            if p.name == profile.name:
                self.profiles[i] = profile
                return
        self.profiles.append(profile)

    def remove(self, name: str) -> None:
        self.profiles = [p for p in self.profiles if p.name != name] or [LLMProfile()]

    @staticmethod
    def profile_from_legacy(data: dict) -> LLMProfile:
        """Build a profile from the old tool's config.json ({"ai_base_url", "ai_model", "ai_api_key", "ai_timeout", ...})."""
        if not isinstance(data, dict) or not (data.get("ai_base_url") or data.get("base_url")):
            raise ValueError("no ai_base_url found in that file")
        base = str(data.get("ai_base_url") or data.get("base_url")).strip()
        host = base.split("//", 1)[-1].split("/", 1)[0]
        return LLMProfile(
            name=f"imported ({host})", base_url=base,
            model=str(data.get("ai_model") or data.get("model") or ""),
            api_key=str(data.get("ai_api_key") or data.get("api_key") or ""),
            timeout=float(data.get("ai_timeout") or data.get("timeout") or 300))

    def import_legacy(self, path: str | Path) -> LLMProfile:
        import json as _json
        with open(Path(path).expanduser(), encoding="utf-8-sig") as f:
            prof = self.profile_from_legacy(_json.load(f))
        self.upsert(prof)
        self.active = prof.name
        self.save()
        return prof

    def remember_vault(self, vault: str) -> None:
        self.recent_vaults = [vault] + [v for v in self.recent_vaults if v != vault][:9]

    @classmethod
    def load(cls) -> "Profiles":
        data = read_json(config_home() / "profiles.json", None)
        if not isinstance(data, dict):
            obj = cls()
            obj.active = obj.profiles[0].name
            return obj
        profs = [_dc_from(LLMProfile, d) for d in data.get("profiles", []) if isinstance(d, dict)] or [LLMProfile()]
        obj = cls(profiles=profs, active=data.get("active") or profs[0].name,
                  recent_vaults=[str(v) for v in data.get("recent_vaults", [])])
        return obj

    def save(self) -> None:
        write_json(self.path, asdict(self), private=True)


# --------------------------------------------------------------------------- vault settings

@dataclass
class Settings:
    # parallelism
    workers: int = 4
    batch_size: int = 1                 # notes per LLM prompt (1 = one note per request)
    max_note_chars: int = 6000          # note text sent to the model is truncated to this
    # auto-mode
    auto_level: str = "assist"          # manual | assist | auto
    auto_accept_threshold: float = 0.85
    auto_apply: bool = False
    auto_apply_cap: int = 25
    rules_auto_accept: bool = True
    watch_interval: float = 15.0        # seconds between disk checks (assist/auto)
    # safety
    collision_policy: str = "suffix"    # suffix | skip   (never overwrites)
    backup: bool = True
    preserve_mtime: bool = True
    prune_empty_dirs: bool = True
    # scanning
    ignore_globs: list[str] = field(default_factory=list)   # hidden completely (dot-folders always ignored)
    no_ai_globs: list[str] = field(default_factory=list)    # visible, but the AI never proposes for them
    date_source: str = "earliest"
    # header
    header_schema: list[FieldSpec] = field(default_factory=default_schema)
    # prompts (empty = built-in default)
    prompts: dict[str, str] = field(default_factory=dict)
    # context sent with routing requests
    context_siblings: int = 6
    context_links: int = 8
    vocab_size: int = 60
    # ui
    theme: str = "textual-dark"

    def spec(self, key: str) -> FieldSpec | None:
        for s in self.header_schema:
            if s.key == key:
                return s
        return None

    @property
    def llm_fields(self) -> list[FieldSpec]:
        return [s for s in self.header_schema if s.source == "llm"]

    def validate(self) -> None:
        self.workers = max(1, min(int(self.workers), 128))
        self.batch_size = max(1, min(int(self.batch_size), 20))
        self.max_note_chars = max(500, int(self.max_note_chars))
        self.auto_accept_threshold = max(0.0, min(float(self.auto_accept_threshold), 1.0))
        if self.auto_level not in AUTO_LEVELS:
            self.auto_level = "assist"
        if self.collision_policy not in COLLISION_POLICIES:
            self.collision_policy = "suffix"
        if self.date_source not in DATE_SOURCES:
            self.date_source = "earliest"
        self.auto_apply_cap = max(1, int(self.auto_apply_cap))
        self.watch_interval = max(2.0, float(self.watch_interval))

    @staticmethod
    def path_for(vault: str | Path) -> Path:
        return Path(vault) / STATE_DIR / "settings.json"

    @classmethod
    def load(cls, vault: str | Path) -> "Settings":
        data = read_json(cls.path_for(vault), None)
        obj = cls()
        if isinstance(data, dict):
            schema = data.pop("header_schema", None)
            for f in fields(cls):
                if f.name in data and f.name != "header_schema":
                    setattr(obj, f.name, data[f.name])
            if isinstance(schema, list) and schema:
                obj.header_schema = [_dc_from(FieldSpec, d) for d in schema if isinstance(d, dict) and d.get("key")]
        obj.validate()
        return obj

    def save(self, vault: str | Path) -> None:
        self.validate()
        write_json(self.path_for(vault), asdict(self))


def _dc_from(cls, data: dict):
    """Build a dataclass from a dict, ignoring unknown keys (forward/backward compatible)."""
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})
