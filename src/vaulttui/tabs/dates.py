"""Date fixer tab — fully offline. Compares each note's header date with the file system and stages fixes."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, Checkbox, DataTable, Input, Label, Select, Static

from vaultcore import dates as dt
from vaultcore.datefmt import parse_date_loose

from ..modals import PromptModal
from .base import TabBase

FILTERS = [("Problems", "problems"), ("All notes", "all"), ("Missing", "missing"), ("Unreadable", "unreadable"),
           ("Wrong format", "format"), ("Later than file", "later"), ("Earlier than file", "earlier"), ("OK", "ok")]
STYLE = {"ok": "green", "missing": "red", "unreadable": "red", "format": "yellow", "later": "bold yellow", "earlier": "cyan"}
ROW_CAP = 3000


class DatesTab(TabBase):
    TAB_ID = "dates"
    MAIN_FOCUS = "#dt-table"
    WATCH = {"scan", "settings", "stage"}
    MIN_INTERVAL = 1.0

    BINDINGS = [
        Binding("space", "toggle_select", "Select"),
        Binding("f", "fix", "Fix cursor/selected"),
        Binding("F", "fix_all", "Fix all shown"),
        Binding("e", "edit", "Set by hand"),
    ]

    def __init__(self, session, **kw):
        super().__init__(session, **kw)
        self.filter = "problems"
        self.sel: set[str] = set()
        self.cur: str | None = None
        self.rows: list[dt.DateRow] = []
        self._loading = True

    def compose(self) -> ComposeResult:
        s = self.session.settings
        with Horizontal(classes="toolbar"):
            yield Label("date comes from")
            yield Select([(f"{k}", k) for k in dt.SOURCES], value=s.date_source if s.date_source in dt.SOURCES else "earliest",
                         allow_blank=False, id="dt-source")
            yield Label("tolerance (days)")
            yield Input("1", id="dt-tol", classes="tiny")
            yield Checkbox("flag earlier too", id="dt-earlier")
            yield Select(FILTERS, value="problems", allow_blank=False, id="dt-filter")
        with Horizontal(classes="toolbar"):
            yield Button("Fix selected / cursor", id="dt-fix", variant="primary")
            yield Button("Fix all shown", id="dt-fixall", variant="success")
            yield Button("Set by hand…", id="dt-edit")
            yield Static("", id="dt-srchelp")
        yield Static("", id="dt-summary")
        yield DataTable(id="dt-table", cursor_type="row", zebra_stripes=True)
        yield Static("[b]space[/] select  [b]f[/] fix cursor/selected  [b]F[/] fix everything shown  [b]e[/] set a date by hand  ·  "
                     "fixes are [b]staged[/] (Stage tab) — nothing is written until you apply, and apply can be undone.  "
                     "[dim]No model, no network.[/]  [dim]'later than file' = the header says the note was created after the file's own "
                     "timestamp, usually a copy-time stamp. If the copy reset BOTH timestamps, no source can recover the real date — use 'by hand'.[/]",
                     classes="helpline")

    def on_mount(self) -> None:
        t = self.query_one("#dt-table", DataTable)
        t.add_column("", width=2, key="sel")
        t.add_column("Status", width=11, key="st")
        t.add_column("Note", width=40, key="note")
        t.add_column("Header date", width=18, key="cur")
        t.add_column("File date", width=17, key="file")
        t.add_column("Δ days", width=8, key="d")
        t.add_column("Proposed", width=17, key="prop")
        self.call_after_refresh(self._ready)
        self.maybe_refresh(force=True)

    def _ready(self) -> None:
        self._loading = False

    # ------------------------------------------------------------------ data
    def _opts(self):
        try:
            tol = float(self.query_one("#dt-tol", Input).value or 1)
        except ValueError:
            tol = 1.0
        return dict(source=str(self.query_one("#dt-source", Select).value),
                    tolerance_days=tol, flag_earlier=self.query_one("#dt-earlier", Checkbox).value)

    def refresh_view(self) -> None:
        s = self.session
        spec = dt.date_spec(s.settings)
        t = self.query_one("#dt-table", DataTable)
        keep = self.cur
        t.clear()
        if spec is None:
            self.query_one("#dt-summary", Static).update("[red]Your header schema has no field of kind 'date'.[/] Add one in Settings ▸ Header schema.")
            return
        o = self._opts()
        self.rows = dt.analyze(s, **o)
        c = dt.counts(self.rows)
        f = self.filter
        shown = [r for r in self.rows if (f == "all" or (f == "problems" and r.status != "ok") or r.status == f)][:ROW_CAP]
        for r in shown:
            d = r.delta_days
            t.add_row(Text("◆", style="bold magenta") if r.rel in self.sel else "",
                      Text(r.status, style=STYLE[r.status]), r.rel, r.current or Text("—", style="dim"),
                      r.file_date.strftime("%Y-%m-%d %H:%M"),
                      "" if d is None else f"{d:+.0f}", Text(r.proposed or "", style="green"), key=r.rel)
        if keep:
            try:
                t.move_cursor(row=t.get_row_index(keep), animate=False)
            except Exception:
                pass
        self.query_one("#dt-summary", Static).update(
            f"field [b]{spec.key}[/] ({spec.date_format}) · " + "  ".join(f"[{STYLE[k]}]{k} {v}[/]" for k, v in c.items())
            + f" · showing {len(shown)} · {len(self.sel)} selected")
        self.query_one("#dt-srchelp", Static).update(f"[dim]{dt.SOURCE_HELP.get(o['source'], '')}[/]")

    def on_data_table_row_highlighted(self, ev: DataTable.RowHighlighted) -> None:
        self.cur = ev.row_key.value if ev.row_key is not None else None

    # ------------------------------------------------------------------ controls
    def on_select_changed(self, ev: Select.Changed) -> None:
        if self._loading:
            return
        if ev.select.id == "dt-filter":
            self.filter = str(ev.value)
        elif ev.select.id == "dt-source":
            st = self.session.settings
            if str(ev.value) != st.date_source:        # the same source is used when the AI fills missing dates
                st.date_source = str(ev.value)
                self.session.save_settings()
                self.run_worker(self.app._rescan())
        self.maybe_refresh(force=True)

    def on_input_changed(self, ev: Input.Changed) -> None:
        if ev.input.id == "dt-tol" and not self._loading:
            self.maybe_refresh(force=True)

    def on_checkbox_changed(self, ev: Checkbox.Changed) -> None:
        if ev.checkbox.id == "dt-earlier" and not self._loading:
            self.maybe_refresh(force=True)

    def on_button_pressed(self, ev: Button.Pressed) -> None:
        {"dt-fix": self.action_fix, "dt-fixall": self.action_fix_all, "dt-edit": self.action_edit}.get(ev.button.id, lambda: None)()

    # ------------------------------------------------------------------ actions
    def action_toggle_select(self) -> None:
        if self.cur:
            self.sel.symmetric_difference_update({self.cur})
            self.maybe_refresh(force=True)
            self.query_one("#dt-table", DataTable).action_cursor_down()

    def _stage(self, rows: list[dt.DateRow]) -> None:
        n = self.session.fix_dates(rows)
        self.sel.clear()
        self.toast(f"staged {n} date fix(es) — review & apply in Stage" if n else "nothing to fix there")
        self.maybe_refresh(force=True)

    def action_fix(self) -> None:
        want = self.sel or ({self.cur} if self.cur else set())
        self._stage([r for r in self.rows if r.rel in want])

    def action_fix_all(self) -> None:
        f = self.filter
        self._stage([r for r in self.rows if r.status != "ok" and (f in ("problems", "all") or r.status == f)])

    def action_edit(self) -> None:
        r = next((x for x in self.rows if x.rel == self.cur), None)
        spec = dt.date_spec(self.session.settings)
        if r is None or spec is None:
            return

        def done(text):
            if not text or not text.strip():
                return
            p = parse_date_loose(text)
            if p is None:
                self.toast("could not read that as a date — try 2024-09-16 or 2024-09-16T17:18", "error")
                return
            self.session.stage_header(r.rel, {spec.key: p.strftime(spec.date_format)}, overwrite=[spec.key], source="manual",
                                      reason="date set by hand")
            self.toast("staged — review & apply in Stage")
            self.maybe_refresh(force=True)
        self.app.push_screen(PromptModal(f"Date for {r.rel}", f"Format {spec.date_format}; the file says {r.file_date:%Y-%m-%d %H:%M}",
                                         r.current or r.file_date.strftime(spec.date_format)), done)
