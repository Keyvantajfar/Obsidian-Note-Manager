"""Tags tab: header-value vocabulary manager — usage counts, near-duplicates, rename / merge / delete across notes."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, Checkbox, DataTable, Input, Select, Static

from ..modals import ConfirmModal, PromptModal
from .base import TabBase


class TagsTab(TabBase):
    TAB_ID = "tags"
    MAIN_FOCUS = "#tv-table"
    WATCH = {"scan", "settings"}
    MIN_INTERVAL = 1.5

    BINDINGS = [
        Binding("space", "toggle_select", "Select"),
        Binding("r", "rename", "Rename"),
        Binding("m", "merge", "Merge selected"),
        Binding("d", "delete", "Delete value"),
    ]

    def __init__(self, session, **kw):
        super().__init__(session, **kw)
        self.key = "tags"
        self.sel: set[str] = set()
        self.cur: str | None = None
        self.only_dups = False
        self.filter = ""

    def compose(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Select(self._key_options(), value=self.key, allow_blank=False, id="tv-key")
            yield Input(placeholder="filter…", id="tv-filter")
            yield Checkbox("only near-duplicates", id="tv-dups")
            yield Button("Rename", id="tv-rename")
            yield Button("Merge selected", id="tv-merge")
            yield Button("Delete", id="tv-delete", variant="error")
        yield Static("", id="tv-summary")
        yield DataTable(id="tv-table", cursor_type="row", zebra_stripes=True)
        yield Static("[b]space[/] select  [b]r[/] rename  [b]m[/] merge the selected values into one  [b]d[/] remove value from all notes  "
                     "·  changes are [b]staged[/] (Stage tab) — nothing is written until you apply", classes="helpline")

    def _key_options(self):
        sch = self.session.settings.header_schema
        return [(f"{s.title} ({s.key})", s.key) for s in sch if s.source != "created"] or [("tags", "tags")]

    def on_mount(self) -> None:
        t = self.query_one("#tv-table", DataTable)
        t.add_column("", width=2, key="sel")
        t.add_column("Value", width=40, key="val")
        t.add_column("Notes", width=7, key="n")
        t.add_column("Near-duplicate group", key="grp")
        self.maybe_refresh(force=True)

    def refresh_view(self) -> None:
        s = self.session
        sel = self.query_one("#tv-key", Select)
        opts = self._key_options()
        if [o[1] for o in opts] != [o for o in getattr(self, "_opts", [])]:
            self._opts = [o[1] for o in opts]
            sel.set_options(opts)
            sel.value = self.key if self.key in self._opts else self._opts[0]
        usage = s.value_usage(self.key)
        groups = s.value_groups(self.key)
        gid = {v: i + 1 for i, g in enumerate(groups) for v in g}
        t = self.query_one("#tv-table", DataTable)
        keep = self.cur
        t.clear()
        q = self.filter.lower()
        shown = 0
        for v, n in usage:
            if q and q not in v.lower():
                continue
            if self.only_dups and v not in gid:
                continue
            g = ""
            if v in gid:
                others = [x for x in groups[gid[v] - 1] if x != v][:3]
                g = Text(f"#{gid[v]}  ≈ " + ", ".join(others), style="yellow")
            t.add_row(Text("◆", style="bold magenta") if v in self.sel else "", v, str(n), g, key=v)
            shown += 1
        if keep:
            try:
                t.move_cursor(row=t.get_row_index(keep), animate=False)
            except Exception:
                pass
        self.query_one("#tv-summary", Static).update(
            f"{len(usage)} distinct value(s) · {len(groups)} near-duplicate group(s) · showing {shown} · {len(self.sel)} selected")

    def on_data_table_row_highlighted(self, ev: DataTable.RowHighlighted) -> None:
        self.cur = ev.row_key.value if ev.row_key is not None else None

    def on_select_changed(self, ev: Select.Changed) -> None:
        if ev.select.id == "tv-key" and str(ev.value) != self.key:
            self.key = str(ev.value)
            self.sel.clear()
            self.maybe_refresh(force=True)

    def on_input_changed(self, ev: Input.Changed) -> None:
        if ev.input.id == "tv-filter":
            self.filter = ev.value
            self.maybe_refresh(force=True)

    def on_checkbox_changed(self, ev: Checkbox.Changed) -> None:
        if ev.checkbox.id == "tv-dups":
            self.only_dups = ev.value
            self.maybe_refresh(force=True)

    def on_button_pressed(self, ev: Button.Pressed) -> None:
        {"tv-rename": self.action_rename, "tv-merge": self.action_merge, "tv-delete": self.action_delete}.get(
            ev.button.id, lambda: None)()

    # ------------------------------------------------------------------ actions
    def _targets(self) -> list[str]:
        return sorted(self.sel) if self.sel else ([self.cur] if self.cur else [])

    def action_toggle_select(self) -> None:
        if self.cur:
            self.sel.symmetric_difference_update({self.cur})
            self.maybe_refresh(force=True)
            self.query_one("#tv-table", DataTable).action_cursor_down()

    def _stage(self, olds: list[str], new: str | None) -> None:
        n = self.session.rename_value(self.key, olds, new)
        self.sel.clear()
        self.toast(f"staged {n} header edit(s) — review & apply in the Stage tab" if n else "no note needed a change")
        self.maybe_refresh(force=True)

    def action_rename(self) -> None:
        tg = self._targets()
        if not tg:
            return
        usage = dict(self.session.value_usage(self.key))
        default = max(tg, key=lambda v: usage.get(v, 0))

        def done(new):
            if new and new.strip():
                self._stage(tg, new.strip())
        self.app.push_screen(PromptModal("Rename" if len(tg) == 1 else f"Merge {len(tg)} values",
                                         f"New {self.key} value for: {', '.join(tg[:6])}" + ("…" if len(tg) > 6 else ""), default), done)

    def action_merge(self) -> None:
        if len(self.sel) < 2:
            self.toast("select two or more values first (space)", "warning")
            return
        self.action_rename()

    def action_delete(self) -> None:
        tg = self._targets()
        if not tg:
            return

        def done(ok):
            if ok:
                self._stage(tg, None)
        self.app.push_screen(ConfirmModal(f"Remove {len(tg)} value(s) from every note that has them?\n{', '.join(tg[:8])}", "Stage removal", "Cancel", danger=True), done)
