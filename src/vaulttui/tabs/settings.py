"""Settings tab: everything is configurable here — LLM connection, auto mode, safety, scanning,
header schema, prompts."""
from __future__ import annotations

import asyncio
import json
import time

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, ContentSwitcher, DataTable, Input, Label, OptionList, Select, Static, Switch, TextArea
from textual.widgets.option_list import Option

from vaultcore.llm import LLMClient, LLMError, auth_hint, clean_key, describe_key, parse_json_loose
from vaultcore.prompts import DEFAULT_PROMPTS
from vaultcore.settings import (AUTO_LEVELS, COLLISION_POLICIES, DATE_SOURCES, PRESETS, FieldSpec, LLMProfile,
                                default_schema)

from ..modals import ConfirmModal, FieldSpecModal, FolderModal
from .base import TabBase, short

SECTIONS = [
    ("llm", "LLM connection"),
    ("auto", "Auto mode & jobs"),
    ("safety", "Safety"),
    ("scan", "Scanning & UI"),
    ("schema", "Header schema"),
    ("prompts", "Prompts"),
]

# (attr, type, label)   type: int | float | bool | lines | choice:a,b,c
AUTO_FIELDS = [
    ("workers", "int", "Parallel workers — requests in flight at once (vLLM handles many; a single llama.cpp slot may not)"),
    ("batch_size", "int", "Notes per request (micro-batching). 1 = one note per request"),
    ("auto_level", "choice:" + ",".join(AUTO_LEVELS), "Mode: manual (you start everything) · assist (AI proposes in the background) · auto (+ auto-accept confident ones)"),
    ("auto_accept_threshold", "float", "Auto-accept threshold 0–1 (confidence is the model's own estimate for now — see README, Future work)"),
    ("auto_apply", "bool", "In auto mode, also write accepted changes to the vault (always undoable)"),
    ("auto_apply_cap", "int", "…but never more than this many changes per batch without asking"),
    ("rules_auto_accept", "bool", "Changes produced by your own rules are accepted automatically"),
    ("watch_interval", "float", "Seconds between checks for notes edited outside this program"),
    ("max_note_chars", "int", "Max characters of a note sent to the model (longer notes are truncated)"),
    ("vocab_size", "int", "How many existing tags / MOCs / statuses to list for the model as vocabulary"),
    ("context_siblings", "int", "Sibling notes shown to the model when routing"),
    ("context_links", "int", "Linked notes (with their folders) shown to the model when routing"),
]
SAFETY_FIELDS = [
    ("collision_policy", "choice:" + ",".join(COLLISION_POLICIES), "When a destination file already exists: suffix = save as 'Name (1).md' · skip = leave the note where it is (never overwrites)"),
    ("backup", "bool", "Copy a note to .vaultmgr/backups before changing its header (needed for header undo)"),
    ("preserve_mtime", "bool", "Keep the note's modified time when only its header changed"),
    ("prune_empty_dirs", "bool", "Remove folders that a move leaves empty (undo recreates them)"),
]
SCAN_FIELDS = [
    ("ignore_globs", "lines", "Ignore completely — one glob per line (e.g. Templates/**). Dot-folders are always ignored"),
    ("no_ai_globs", "lines", "Visible, but the AI never proposes for these — one glob per line (e.g. Daily/**)"),
    ("date_source", "choice:" + ",".join(DATE_SOURCES), "Where a 'created' header date comes from (Linux often has no real creation time → falls back to modified time)"),
    ("theme", "choice:textual-dark,textual-light,nord,gruvbox,tokyo-night,monokai,dracula,catppuccin-mocha,solarized-light", "UI theme"),
]
FIELD_SETS = {"auto": AUTO_FIELDS, "safety": SAFETY_FIELDS, "scan": SCAN_FIELDS}


class SettingsTab(TabBase):
    TAB_ID = "settings"
    MAIN_FOCUS = "#se-nav"
    WATCH = {"settings"}
    MIN_INTERVAL = 0.5

    def __init__(self, session, **kw):
        super().__init__(session, **kw)
        self._schema_cur: str | None = None
        self._loading = False

    # ------------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        s = self.session
        with Horizontal():
            nav = OptionList(*[Option(label, id=sid) for sid, label in SECTIONS], id="se-nav")
            yield nav
            with ContentSwitcher(initial="se-llm", id="se-switch"):
                yield from self._compose_llm()
                for sid, fields in FIELD_SETS.items():
                    yield from self._compose_form(sid, fields)
                yield from self._compose_schema()
                yield from self._compose_prompts()

    def _compose_llm(self):
        with VerticalScroll(id="se-llm", classes="form"):
            yield Label("Saved profile")
            yield Select([("-", "-")], id="se-llm-profile", allow_blank=False)
            yield Label("Preset (fills the URL)")
            yield Select([(k, k) for k in PRESETS], id="se-llm-preset", allow_blank=True, prompt="choose a preset…")
            yield Label("Profile name")
            yield Input(id="se-llm-name")
            yield Label("Base URL (OpenAI-compatible, usually ends in /v1)")
            yield Input(id="se-llm-url")
            yield Label("Model name")
            yield Input(id="se-llm-model")
            yield Label("API key (stored in your user config dir with 600 permissions, never inside the vault)")
            yield Input(id="se-llm-key", password=True)
            yield Label("…or name of an environment variable holding the key (preferred)")
            yield Input(id="se-llm-keyenv")
            with Horizontal(classes="row"):
                with Vertical():
                    yield Label("Temperature")
                    yield Input(id="se-llm-temp")
                with Vertical():
                    yield Label("Max tokens")
                    yield Input(id="se-llm-maxtok")
                with Vertical():
                    yield Label("Timeout (s)")
                    yield Input(id="se-llm-timeout")
                with Vertical():
                    yield Label("Retries")
                    yield Input(id="se-llm-retries")
            yield Label("JSON mode (response_format=json_object): auto tries it and remembers if the server refuses")
            yield Select([("auto", "auto"), ("on", "on"), ("off", "off")], value="auto", allow_blank=False, id="se-llm-json")
            yield Label("Thinking / reasoning models: auto = ask the server to skip thinking if a reply comes back empty; "
                        "off = always skip; on = never touch")
            yield Select([("auto", "auto"), ("off", "off"), ("on", "on")], value="auto", allow_blank=False, id="se-llm-reason")
            yield Label('Extra request body (JSON) — e.g. vLLM {"guided_decoding_backend": "xgrammar"} or {"chat_template_kwargs": {"enable_thinking": false}}')
            yield TextArea("{}", id="se-llm-body", classes="small-area")
            yield Label("Extra headers (JSON)")
            yield TextArea("{}", id="se-llm-headers", classes="small-area")
            with Horizontal(classes="buttons"):
                yield Button("Save & use", variant="primary", id="se-llm-save")
                yield Button("Test connection", id="se-llm-test")
                yield Button("Fetch models", id="se-llm-models")
                yield Button("New profile", id="se-llm-new")
                yield Button("Delete profile", variant="error", id="se-llm-del")
            yield Static("", id="se-llm-result")

    def _compose_form(self, sid: str, fields):
        with VerticalScroll(id=f"se-{sid}", classes="form"):
            for attr, typ, label in fields:
                yield Label(label)
                if typ == "bool":
                    with Horizontal(classes="switchrow"):
                        yield Switch(id=f"f-{attr}")
                elif typ.startswith("choice:"):
                    opts = typ.split(":", 1)[1].split(",")
                    yield Select([(o, o) for o in opts], allow_blank=False, id=f"f-{attr}", value=opts[0])
                elif typ == "lines":
                    yield TextArea("", id=f"f-{attr}", classes="small-area")
                else:
                    yield Input(id=f"f-{attr}")
            with Horizontal(classes="buttons"):
                yield Button("Save", variant="primary", id=f"se-save-{sid}")
                yield Button("Reset to defaults", id=f"se-reset-{sid}")

    def _compose_schema(self):
        with Vertical(id="se-schema", classes="form"):
            yield Static("Header fields the tool reads, fills and writes. Order = order in the header. "
                         "Change the default four freely — a note only ever gets the missing keys appended.", classes="pane-title")
            yield DataTable(id="se-schema-table", cursor_type="row", zebra_stripes=True)
            with Horizontal(classes="buttons"):
                yield Button("Add field", variant="primary", id="se-sc-add")
                yield Button("Edit", id="se-sc-edit")
                yield Button("Delete", variant="error", id="se-sc-del")
                yield Button("Up", id="se-sc-up")
                yield Button("Down", id="se-sc-down")
                yield Button("Reset to defaults", id="se-sc-reset")

    def _compose_prompts(self):
        with VerticalScroll(id="se-prompts", classes="form"):
            yield Static("Persona/guideline text sent with every request. The JSON output format is generated from your header schema and "
                         "can't be edited here, so the parser always matches.", classes="pane-title")
            for key, label in (("analyze_base", "Tagging & routing"), ("structure_base", "Structure proposal"), ("rules_base", "Instruction → rules")):
                yield Label(label)
                yield TextArea("", id=f"p-{key}", classes="mid-area")
            with Horizontal(classes="buttons"):
                yield Button("Save prompts", variant="primary", id="se-pr-save")
                yield Button("Reset to defaults", id="se-pr-reset")

    # ------------------------------------------------------------------ populate
    def on_mount(self) -> None:
        t = self.query_one("#se-schema-table", DataTable)
        t.add_column("#", width=3)
        t.add_column("Key", width=14)
        t.add_column("Kind", width=14)
        t.add_column("Source", width=9)
        t.add_column("Req", width=4)
        t.add_column("Instruction")
        self.query_one("#se-nav", OptionList).highlighted = 0
        self.maybe_refresh(force=True)

    def refresh_view(self) -> None:
        """Refresh without clobbering what the user is typing: forms are filled once (and after resets);
        the LLM form is only refilled when the *active profile* changed (new / deleted / imported / switched)."""
        self._loading = True
        try:
            self._load_llm()
            if not getattr(self, "_forms_loaded", False):
                for sid, fields in FIELD_SETS.items():
                    self._load_form(fields)
                self._forms_loaded = True
            if not getattr(self, "_prompts_loaded", False):
                for key in DEFAULT_PROMPTS:
                    self.query_one(f"#p-{key}", TextArea).load_text(self.session.settings.prompts.get(key) or DEFAULT_PROMPTS[key])
                self._prompts_loaded = True
            self._load_schema()
        finally:
            self.call_after_refresh(self._done_loading)

    def _done_loading(self) -> None:
        self._loading = False

    def _load_form(self, fields) -> None:
        st = self.session.settings
        for attr, typ, _ in fields:
            w = self.query_one(f"#f-{attr}")
            val = getattr(st, attr)
            if typ == "bool":
                w.value = bool(val)
            elif typ.startswith("choice:"):
                if val in typ.split(":", 1)[1].split(","):
                    w.value = val
            elif typ == "lines":
                w.load_text("\n".join(val))
            else:
                w.value = str(val)

    def _load_llm(self) -> None:
        pf = self.session.profiles
        sel = self.query_one("#se-llm-profile", Select)
        names = [p.name for p in pf.profiles]
        active = pf.get_active()
        with sel.prevent(Select.Changed):                 # programmatic changes must never look like a user choice
            if [o for o in getattr(self, "_profile_names", [])] != names:
                sel.set_options([(n, n) for n in names])
                self._profile_names = list(names)
            if getattr(self, "_last_active", None) != active.name or getattr(self, "_shown_profile", None) not in names:
                sel.value = active.name if active.name in names else names[0]
        if getattr(self, "_last_active", None) != active.name:          # active profile changed (save/new/delete/import)
            self._last_active = active.name
            self._fill_profile(active)

    def _fill_profile(self, p: LLMProfile) -> None:
        q = self.query_one
        self._shown_profile = p.name
        q("#se-llm-name", Input).value = p.name
        q("#se-llm-url", Input).value = p.base_url
        q("#se-llm-model", Input).value = p.model
        q("#se-llm-key", Input).value = p.api_key
        q("#se-llm-keyenv", Input).value = p.api_key_env
        q("#se-llm-temp", Input).value = str(p.temperature)
        q("#se-llm-maxtok", Input).value = str(p.max_tokens)
        q("#se-llm-timeout", Input).value = str(p.timeout)
        q("#se-llm-retries", Input).value = str(p.max_retries)
        q("#se-llm-json", Select).value = p.json_mode if p.json_mode in ("auto", "on", "off") else "auto"
        q("#se-llm-reason", Select).value = p.reasoning if p.reasoning in ("auto", "off", "on") else "auto"
        q("#se-llm-body", TextArea).load_text(json.dumps(p.extra_body or {}, indent=2))
        q("#se-llm-headers", TextArea).load_text(json.dumps(p.extra_headers or {}, indent=2))

    def _read_profile(self) -> LLMProfile:
        q = self.query_one
        body = json.loads(q("#se-llm-body", TextArea).text.strip() or "{}")
        headers = json.loads(q("#se-llm-headers", TextArea).text.strip() or "{}")
        if not isinstance(body, dict) or not isinstance(headers, dict):
            raise ValueError("extra body / headers must be JSON objects")
        name = q("#se-llm-name", Input).value.strip() or "profile"
        return LLMProfile(
            name=name, base_url=q("#se-llm-url", Input).value.strip(), model=q("#se-llm-model", Input).value.strip(),
            api_key=clean_key(q("#se-llm-key", Input).value), api_key_env=q("#se-llm-keyenv", Input).value.strip(),
            temperature=float(q("#se-llm-temp", Input).value or 0.2), max_tokens=int(q("#se-llm-maxtok", Input).value or 2048),
            timeout=float(q("#se-llm-timeout", Input).value or 300), max_retries=int(q("#se-llm-retries", Input).value or 3),
            json_mode=str(q("#se-llm-json", Select).value), reasoning=str(q("#se-llm-reason", Select).value), extra_body=body, extra_headers={str(k): str(v) for k, v in headers.items()})

    def _load_schema(self) -> None:
        t = self.query_one("#se-schema-table", DataTable)
        keep = self._schema_cur
        t.clear()
        for i, sp in enumerate(self.session.settings.header_schema):
            t.add_row(str(i + 1), sp.key, sp.kind, sp.source, "yes" if sp.required else "", short(sp.instruction or sp.default, 80), key=sp.key)
        if keep:
            try:
                t.move_cursor(row=t.get_row_index(keep), animate=False)
            except Exception:
                pass

    # ------------------------------------------------------------------ navigation & selects
    def on_option_list_option_highlighted(self, ev: OptionList.OptionHighlighted) -> None:
        if ev.option_list.id == "se-nav" and ev.option is not None:
            self.query_one("#se-switch", ContentSwitcher).current = f"se-{ev.option.id}"

    def on_select_changed(self, ev: Select.Changed) -> None:
        if self._loading:
            return
        if ev.select.id == "se-llm-profile":
            if str(ev.value) == getattr(self, "_shown_profile", None):
                return
            p = next((x for x in self.session.profiles.profiles if x.name == ev.value), None)
            if p:
                self._fill_profile(p)
        elif ev.select.id == "se-llm-preset" and ev.value in PRESETS:
            self.query_one("#se-llm-url", Input).value = PRESETS[str(ev.value)]["base_url"]

    def on_data_table_row_highlighted(self, ev: DataTable.RowHighlighted) -> None:
        if ev.data_table.id == "se-schema-table":
            self._schema_cur = ev.row_key.value if ev.row_key is not None else None

    # ------------------------------------------------------------------ buttons
    def on_button_pressed(self, ev: Button.Pressed) -> None:
        bid = ev.button.id or ""
        if bid.startswith("se-save-"):
            self._save_form(bid.removeprefix("se-save-"))
        elif bid.startswith("se-reset-"):
            self._reset_form(bid.removeprefix("se-reset-"))
        elif bid == "se-llm-save":
            self._save_profile()
        elif bid == "se-llm-test":
            self._test()
        elif bid == "se-llm-models":
            self._models()
        elif bid == "se-llm-new":
            self._new_profile()
        elif bid == "se-llm-del":
            self._del_profile()
        elif bid == "se-pr-save":
            self._save_prompts(reset=False)
        elif bid == "se-pr-reset":
            self._save_prompts(reset=True)
        elif bid.startswith("se-sc-"):
            self._schema_action(bid.removeprefix("se-sc-"))

    # ---- generic forms
    def _save_form(self, sid: str) -> None:
        s = self.session
        st = s.settings
        rescan = False
        try:
            for attr, typ, _ in FIELD_SETS[sid]:
                w = self.query_one(f"#f-{attr}")
                if typ == "bool":
                    new = bool(w.value)
                elif typ.startswith("choice:"):
                    new = str(w.value)
                elif typ == "lines":
                    new = [l.strip() for l in w.text.splitlines() if l.strip()]
                elif typ == "int":
                    new = int(w.value)
                else:
                    new = float(w.value)
                if attr in ("ignore_globs", "date_source") and new != getattr(st, attr):
                    rescan = True
                setattr(st, attr, new)
        except ValueError as e:
            self.toast(f"invalid value: {e}", "error")
            return
        s.save_settings()
        for j in s.jobs.active():
            j.set_workers(st.workers)
        try:
            self.app.theme = st.theme
        except Exception:
            pass
        self.toast("settings saved" + (" — rescanning…" if rescan else ""))
        if rescan:
            self.run_worker(self.app._rescan())

    def _reset_form(self, sid: str) -> None:
        from vaultcore.settings import Settings
        d = Settings()
        for attr, _, _ in FIELD_SETS[sid]:
            setattr(self.session.settings, attr, getattr(d, attr))
        self.session.save_settings()
        self.maybe_refresh(force=True)
        self.toast("defaults restored")

    # ---- LLM profile
    def _save_profile(self) -> None:
        try:
            p = self._read_profile()
        except (ValueError, json.JSONDecodeError) as e:
            self.toast(f"invalid profile: {e}", "error")
            return
        old = self.query_one("#se-llm-profile", Select).value
        if old != p.name and old in [x.name for x in self.session.profiles.profiles]:
            self.session.profiles.remove(str(old))
        self.session.use_profile(p)
        self.maybe_refresh(force=True)
        self.toast(f"profile '{p.name}' saved and active")

    def _new_profile(self) -> None:
        names = {p.name for p in self.session.profiles.profiles}
        n = 2
        while f"profile {n}" in names:
            n += 1
        self.session.profiles.upsert(LLMProfile(name=f"profile {n}"))
        self.session.profiles.save()
        self.session.profiles.active = f"profile {n}"
        self.maybe_refresh(force=True)

    def _del_profile(self) -> None:
        name = str(self.query_one("#se-llm-profile", Select).value)

        def done(ok):
            if ok:
                pf = self.session.profiles
                pf.remove(name)
                pf.active = pf.profiles[0].name
                pf.save()
                self.session._llm = None
                self.maybe_refresh(force=True)
        self.app.push_screen(ConfirmModal(f"Delete profile '{name}'?", "Delete", "Cancel", danger=True), done)

    def _test(self) -> None:
        try:
            p = self._read_profile()
        except (ValueError, json.JSONDecodeError) as e:
            self.toast(f"invalid profile: {e}", "error")
            return
        self.query_one("#se-llm-result", Static).update("[dim]testing…[/]")
        self.run_worker(self._do_test(p), exclusive=True, group="llmtest")

    async def _do_test(self, p: LLMProfile) -> None:
        out = self.query_one("#se-llm-result", Static)
        p.max_retries = 0
        c = LLMClient(p)
        lines = [f"[dim]{describe_key(clean_key(p.resolved_key()))}[/]"]
        try:
            try:
                models = await c.list_models()
                lines.append(f"[green]✔[/] server reachable — {len(models)} model(s)" + (f": {', '.join(models[:5])}" if models else ""))
            except LLMError as e:
                lines.append(f"[yellow]![/] /models failed ({e}) — trying a chat request anyway")
            t0 = time.monotonic()
            r = await c.chat("You are a connection test.", 'Reply with exactly this JSON: {"ok": true}', json_mode=True, max_tokens=600)
            if not r.text.strip():
                c.note_empty_answer()
                lines.append(f"[red]✘[/] the server answered but with EMPTY text (finish_reason={r.finish_reason or '?'}"
                             + (", the model spent its tokens on hidden reasoning — set Thinking to 'off' or raise max tokens" if r.reasoning or r.finish_reason == "length" else "")
                             + ")")
            elif parse_json_loose(r.text) is None:
                lines.append(f"[yellow]![/] chat works ({time.monotonic() - t0:.2f}s) but the reply is not JSON: {short(r.text, 80)!r} — set JSON mode to 'off' or choose another model")
            else:
                lines.append(f"[green]✔[/] chat + JSON works — {time.monotonic() - t0:.2f}s, reply: {short(r.text, 60)}")
        except LLMError as e:
            lines.append(f"[red]✘[/] {e}")
            if auth_hint(str(e)):
                lines.append(f"[yellow]→ {auth_hint(str(e))}[/]  (listing models worked because OpenRouter's /models is public — it proves nothing about the key)")
        except Exception as e:  # noqa: BLE001
            lines.append(f"[red]✘[/] {type(e).__name__}: {e}")
        finally:
            await c.aclose()
        out.update("\n".join(lines))

    def _models(self) -> None:
        try:
            p = self._read_profile()
        except (ValueError, json.JSONDecodeError) as e:
            self.toast(f"invalid profile: {e}", "error")
            return

        async def go():
            c = LLMClient(p)
            try:
                models = await c.list_models()
            except Exception as e:  # noqa: BLE001
                self.toast(f"could not list models: {e}", "error")
                return
            finally:
                await c.aclose()

            def done(m):
                if m:
                    self.query_one("#se-llm-model", Input).value = m
            self.app.push_screen(FolderModal(models, p.model, "Pick the model", allow_new=True), done)
        self.run_worker(go(), exclusive=True, group="llmmodels")

    # ---- schema
    def _schema_action(self, what: str) -> None:
        s = self.session
        sch = s.settings.header_schema
        idx = next((i for i, x in enumerate(sch) if x.key == self._schema_cur), None)

        def commit(msg: str) -> None:
            s.save_settings()
            self.run_worker(self._reload_schema())
            self._load_schema()
            self.toast(msg)

        if what == "add":
            def done(sp):
                if sp:
                    if any(x.key.lower() == sp.key.lower() for x in sch):
                        self.toast("a field with that key already exists", "error")
                        return
                    sch.append(sp)
                    self._schema_cur = sp.key
                    commit(f"field '{sp.key}' added")
            self.app.push_screen(FieldSpecModal(), done)
        elif what == "edit" and idx is not None:
            def done_e(sp):
                if sp:
                    sch[idx] = sp
                    self._schema_cur = sp.key
                    commit(f"field '{sp.key}' updated")
            self.app.push_screen(FieldSpecModal(sch[idx]), done_e)
        elif what == "del" and idx is not None:
            def done_d(ok):
                if ok:
                    sch.pop(idx)
                    commit("field removed (existing headers in your notes are not touched)")
            self.app.push_screen(ConfirmModal(f"Remove field '{sch[idx].key}' from the schema?\nNotes keep whatever is already in their headers.", "Remove", "Cancel", danger=True), done_d)
        elif what in ("up", "down") and idx is not None:
            j = idx + (-1 if what == "up" else 1)
            if 0 <= j < len(sch):
                sch[idx], sch[j] = sch[j], sch[idx]
                commit("order changed")
        elif what == "reset":
            def done_r(ok):
                if ok:
                    s.settings.header_schema = default_schema()
                    commit("schema reset to the defaults (parent / status / tags / date)")
            self.app.push_screen(ConfirmModal("Reset the header schema to the four default fields?", "Reset", "Cancel"), done_r)

    async def _reload_schema(self) -> None:
        await asyncio.to_thread(self.session.index.reload_schema)
        self.session.analyzer.invalidate()
        self.session.emit("scan", diff=None)

    # ---- prompts
    def _save_prompts(self, reset: bool) -> None:
        st = self.session.settings
        if reset:
            st.prompts = {}
            self._prompts_loaded = False
        else:
            for key, default in DEFAULT_PROMPTS.items():
                txt = self.query_one(f"#p-{key}", TextArea).text.strip()
                if txt and txt != default.strip():
                    st.prompts[key] = txt
                else:
                    st.prompts.pop(key, None)
        self.session.save_settings()
        self.maybe_refresh(force=True)
        self.toast("prompts reset" if reset else "prompts saved — notes will be re-analysed with the new prompt (old cached answers are not reused)")
