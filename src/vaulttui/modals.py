"""Small reusable modal dialogs."""
from __future__ import annotations

from typing import Callable

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, OptionList, Select, Static, TextArea
from textual.widgets.option_list import Option

from vaultcore.frontmatter import normalize_value
from vaultcore.settings import FieldSpec


class _Modal(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def action_cancel(self) -> None:
        self.dismiss(None)


class PromptModal(_Modal):
    def __init__(self, title: str, label: str = "", default: str = "", placeholder: str = "", password: bool = False):
        super().__init__()
        self._title, self._label, self._default, self._ph, self._pw = title, label, default, placeholder, password

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self._title, classes="dialog-title")
            if self._label:
                yield Static(self._label, classes="dialog-text")
            yield Input(self._default, placeholder=self._ph, password=self._pw, id="prompt-input")
            with Horizontal(classes="dialog-buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    def on_input_submitted(self, ev: Input.Submitted) -> None:
        self.dismiss(ev.value)

    def on_button_pressed(self, ev: Button.Pressed) -> None:
        self.dismiss(self.query_one("#prompt-input", Input).value if ev.button.id == "ok" else None)


class ConfirmModal(_Modal):
    def __init__(self, text: str, yes: str = "Yes", no: str = "No", danger: bool = False):
        super().__init__()
        self._text, self._yes, self._no, self._danger = text, yes, no, danger

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self._text, classes="dialog-text")
            with Horizontal(classes="dialog-buttons"):
                yield Button(self._yes, variant="error" if self._danger else "primary", id="yes")
                yield Button(self._no, id="no")

    def on_button_pressed(self, ev: Button.Pressed) -> None:
        self.dismiss(ev.button.id == "yes")

    def key_y(self) -> None:
        self.dismiss(True)

    def key_n(self) -> None:
        self.dismiss(False)


class InfoModal(_Modal):
    def __init__(self, title: str, body: str | Text, *, wide: bool = False):
        super().__init__()
        self._title, self._body, self._wide = title, body, wide

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide" if self._wide else "dialog"):
            yield Label(self._title, classes="dialog-title")
            with VerticalScroll(classes="dialog-scroll"):
                yield Static(self._body, classes="dialog-text")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Close", variant="primary", id="close")

    def on_button_pressed(self, ev: Button.Pressed) -> None:
        self.dismiss(True)

    def key_enter(self) -> None:
        self.dismiss(True)


class FolderModal(_Modal):
    """Pick (or type) a folder path, with live filtering over known folders."""

    def __init__(self, folders: list[str], default: str = "", title: str = "Move to folder",
                 allow_new: bool = True, descriptions: dict[str, str] | None = None):
        super().__init__()
        self.folders = sorted(set(folders), key=str.lower)
        self._default, self._title, self.allow_new = default, title, allow_new
        self.desc = descriptions or {}

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Label(self._title, classes="dialog-title")
            yield Input(self._default, placeholder="type to filter — or type a new path", id="folder-input")
            yield OptionList(id="folder-list")
            with Horizontal(classes="dialog-buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self._fill(self._default)
        self.query_one("#folder-input", Input).focus()

    def _fill(self, q: str) -> None:
        ol = self.query_one("#folder-list", OptionList)
        ol.clear_options()
        ql = q.strip().lower()
        hits = [f for f in self.folders if not ql or ql in f.lower()][:200]
        for f in hits:
            d = self.desc.get(f)
            ol.add_option(Option(f"{f}" + (f"  [dim]— {d[:50]}[/]" if d else ""), id=f))
        if hits:
            ol.highlighted = 0

    def on_input_changed(self, ev: Input.Changed) -> None:
        self._fill(ev.value)

    def on_input_submitted(self, ev: Input.Submitted) -> None:
        self._finish(ev.value)

    def on_option_list_option_selected(self, ev: OptionList.OptionSelected) -> None:
        self.dismiss(ev.option.id)

    def key_down(self) -> None:
        self.query_one("#folder-list", OptionList).focus()

    def _finish(self, typed: str) -> None:
        typed = typed.strip().strip("/")
        ol = self.query_one("#folder-list", OptionList)
        if typed in self.folders or (self.allow_new and typed):
            self.dismiss(typed)
        elif ol.highlighted is not None and ol.option_count:
            self.dismiss(ol.get_option_at_index(ol.highlighted).id)
        elif not typed:
            self.dismiss("")

    def on_button_pressed(self, ev: Button.Pressed) -> None:
        if ev.button.id == "ok":
            self._finish(self.query_one("#folder-input", Input).value)
        else:
            self.dismiss(None)


class FieldsModal(_Modal):
    """Edit header fields by hand. Returns {key: value} for the fields that changed."""

    def __init__(self, title: str, schema: list[FieldSpec], values: dict[str, list[str]]):
        super().__init__()
        self._title, self.schema, self.values = title, schema, values

    @staticmethod
    def as_text(v: list[str] | None) -> str:
        return ", ".join(v or [])

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Label(self._title, classes="dialog-title")
            with VerticalScroll(classes="dialog-scroll"):
                for sp in self.schema:
                    hint = "comma-separated" if sp.is_list else sp.kind
                    yield Label(f"{sp.title}  [dim]({hint})[/]")
                    cur = next((v for k, v in self.values.items() if k.lower() == sp.key.lower()), [])
                    yield Input(self.as_text(cur), id=f"f-{sp.key}")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Stage edit", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, ev: Button.Pressed) -> None:
        if ev.button.id != "ok":
            self.dismiss(None)
            return
        out = {}
        for sp in self.schema:
            raw = self.query_one(f"#f-{sp.key}", Input).value
            new = normalize_value(sp, raw)
            cur = next((v for k, v in self.values.items() if k.lower() == sp.key.lower()), [])
            old = normalize_value(sp, ", ".join(cur) if sp.is_list else (cur[0] if cur else ""))
            if new != old:
                out[sp.key] = new
        self.dismiss(out)


class StructureModal(_Modal):
    """Review an AI-proposed folder structure: accept (replace / merge), give feedback, or cancel."""

    def __init__(self, folders, note: str, counts: dict[str, int] | None = None):
        super().__init__()
        self.folders, self.note = folders, note

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Label("Proposed folder structure", classes="dialog-title")
            if self.note:
                yield Static(self.note, classes="dialog-text")
            t = DataTable(id="struct-table", cursor_type="row", zebra_stripes=True)
            t.add_columns("Folder", "Description")
            for f in self.folders:
                t.add_row(f.path, f.description)
            yield t
            yield Input(placeholder="feedback to revise it (e.g. 'merge Research and AI, add a Projects folder') — Enter to send", id="feedback")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Use as plan (replace)", variant="primary", id="replace")
                yield Button("Add to plan (merge)", id="merge")
                yield Button("Cancel", id="cancel")

    def on_input_submitted(self, ev: Input.Submitted) -> None:
        if ev.value.strip():
            self.dismiss(("feedback", ev.value.strip()))

    def on_button_pressed(self, ev: Button.Pressed) -> None:
        self.dismiss({"replace": ("replace", None), "merge": ("merge", None)}.get(ev.button.id))


class ApplyModal(_Modal):
    """Dry-run summary before anything touches the vault."""

    def __init__(self, plan, applies: str = "Apply"):
        super().__init__()
        self.plan, self._applies = plan, applies

    def compose(self) -> ComposeResult:
        c = self.plan.counts()
        with Vertical(classes="dialog wide"):
            yield Label("Dry run — nothing has been written yet", classes="dialog-title")
            yield Static(
                f"[b]{c['headers']}[/] header write(s) · [b]{c['moves']}[/] move(s) · [b]{c['new_folders']}[/] new folder(s)"
                f" · [yellow]{c['warnings']}[/] with warnings · [red]{c['blocked']}[/] blocked · {c['noop']} already done",
                classes="dialog-text")
            t = DataTable(cursor_type="row", zebra_stripes=True, id="plan-table")
            t.add_columns("", "Kind", "Note", "→ Destination / issues")
            for it in self.plan.items:
                if it.noop:
                    continue
                mark = Text("✘", style="red") if it.blocked else (Text("!", style="yellow") if it.warned else Text("✔", style="green"))
                issues = "; ".join(i.text for i in it.issues if i.level != "info")
                t.add_row(mark, it.kind, it.note, (it.dst or "") + ((" — " if it.dst and issues else "") + issues))
            yield t
            with Horizontal(classes="dialog-buttons"):
                yield Button(f"{self._applies} {c['headers'] + c['moves']} change(s)", variant="primary", id="apply",
                             disabled=(c["headers"] + c["moves"] == 0))
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, ev: Button.Pressed) -> None:
        self.dismiss(ev.button.id == "apply")


class FieldSpecModal(_Modal):
    """Create / edit one header-schema field."""

    def __init__(self, spec: FieldSpec | None = None):
        super().__init__()
        self.spec = spec or FieldSpec(key="", kind="text", source="llm")
        self.is_new = spec is None

    def compose(self) -> ComposeResult:
        from textual.widgets import Switch
        from vaultcore.settings import FIELD_KINDS, FIELD_SOURCES
        sp = self.spec
        with Vertical(classes="dialog wide"):
            yield Label("New header field" if self.is_new else f"Edit field '{sp.key}'", classes="dialog-title")
            with VerticalScroll(classes="dialog-scroll"):
                yield Label("Key (the name written in the note header)")
                yield Input(sp.key, id="fs-key")
                yield Label("Label (shown in the UI)")
                yield Input(sp.label, id="fs-label")
                yield Label("Kind — how the value is written")
                yield Select([(k, k) for k in FIELD_KINDS], value=sp.kind, allow_blank=False, id="fs-kind")
                yield Label("Source — who fills it (llm / created / modified / static / none)")
                yield Select([(k, k) for k in FIELD_SOURCES], value=sp.source, allow_blank=False, id="fs-source")
                yield Label("Instruction for the model (what a good value looks like)")
                yield Input(sp.instruction, id="fs-ins")
                yield Label("Default / static value")
                yield Input(sp.default, id="fs-default")
                yield Label("Allowed values (comma-separated, empty = anything)")
                yield Input(", ".join(sp.allowed), id="fs-allowed")
                yield Label("Min items / max items (list kinds)")
                with Horizontal():
                    yield Input(str(sp.min_items), id="fs-min", classes="tiny")
                    yield Input(str(sp.max_items), id="fs-max", classes="tiny")
                yield Label("Date format (date kind)")
                yield Input(sp.date_format, id="fs-datefmt")
                with Horizontal(classes="switchrow"):
                    yield Switch(sp.required, id="fs-required")
                    yield Label("required (counts as 'missing' when absent)")
                with Horizontal(classes="switchrow"):
                    yield Switch(sp.quote, id="fs-quote")
                    yield Label('wrap wikilink in quotes  ("[[X]]")')
            with Horizontal(classes="dialog-buttons"):
                yield Button("Save field", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, ev: Button.Pressed) -> None:
        from textual.widgets import Switch
        if ev.button.id != "ok":
            self.dismiss(None)
            return
        q = lambda i: self.query_one(i)  # noqa: E731
        key = q("#fs-key").value.strip()
        if not key or any(c in key for c in ":#[]{}\"' ") and " " not in key:
            self.app.notify("key must be a simple name (no spaces or :#[]{})", severity="error")
            return
        try:
            mn, mx = int(q("#fs-min").value or 0), int(q("#fs-max").value or 0)
        except ValueError:
            self.app.notify("min/max items must be whole numbers", severity="error")
            return
        self.dismiss(FieldSpec(
            key=key, label=q("#fs-label").value.strip(), kind=str(q("#fs-kind").value), source=str(q("#fs-source").value),
            instruction=q("#fs-ins").value.strip(), default=q("#fs-default").value.strip(),
            allowed=[a.strip() for a in q("#fs-allowed").value.split(",") if a.strip()],
            min_items=mn, max_items=mx, date_format=q("#fs-datefmt").value or "%Y-%m-%dT%H:%M",
            required=q("#fs-required").value, quote=q("#fs-quote").value))
