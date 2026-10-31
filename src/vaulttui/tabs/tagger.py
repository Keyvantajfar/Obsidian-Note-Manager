"""Tagger tab: run the AI over notes in parallel, review/accept/edit proposed headers as they stream in."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, Checkbox, DataTable, Input, Label, Select, Static

from ..modals import FieldsModal, InfoModal
from .base import TabBase, conf_text, header_state, short, status_text

FILTERS = [
    ("Needs header (not yet proposed)", "needs"),
    ("All notes", "all"),
    ("Proposed", "proposed"),
    ("Accepted", "accepted"),
    ("Rejected", "rejected"),
    ("No header at all", "noheader"),
    ("Low confidence", "lowconf"),
    ("Had an error", "errors"),
    ("Locked", "locked"),
]
ROW_CAP = 2500


def progress_bar(frac: float, width: int = 28) -> str:
    n = int(round(max(0.0, min(1.0, frac)) * width))
    return "█" * n + "░" * (width - n)


class TaggerTab(TabBase):
    TAB_ID = "tagger"
    MAIN_FOCUS = "#tg-table"
    WATCH = {"scan", "stage", "job", "settings"}
    MIN_INTERVAL = 0.8

    BINDINGS = [
        Binding("space", "toggle_select", "Select"),
        Binding("a", "accept", "Accept"),
        Binding("x", "reject", "Reject"),
        Binding("e", "edit", "Edit"),
        Binding("d", "unstage", "Unstage"),
        Binding("A", "accept_conf", "Accept ≥ threshold"),
        Binding("R", "run_missing", "Run on all missing"),
        Binding("r", "rerun", "Re-run (fresh)"),
        Binding("l", "lock", "Lock"),
    ]

    def __init__(self, session, **kw):
        super().__init__(session, **kw)
        self.sel: set[str] = set()
        self.filter = "needs"
        self.search = ""
        self.cur: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="tg-bar", classes="toolbar"):
            yield Select(FILTERS, value="needs", allow_blank=False, id="tg-filter")
            yield Input(placeholder="search path…", id="tg-search")
            yield Checkbox("+ route", id="tg-route", value=False)
            yield Button("Run on missing", variant="primary", id="tg-run")
            yield Button("Re-run selected", id="tg-rerun")
            yield Button("Accept ≥ thr", id="tg-acc")
            yield Label("workers")
            yield Input(str(self.session.settings.workers), id="tg-workers", classes="tiny")
            yield Label("per request")
            yield Input(str(self.session.settings.batch_size), id="tg-batch", classes="tiny")
        yield Static("", id="tg-progress")
        yield DataTable(id="tg-table", cursor_type="row", zebra_stripes=True)
        yield Static("[b]space[/] select  [b]a[/]/[b]x[/] accept/reject  [b]e[/] edit by hand  [b]d[/] unstage  [b]A[/] accept all ≥ threshold  "
                     "[b]R[/] run on all missing  [b]r[/] re-run selection  [b]Enter[/] preview  [b]p[/] pause jobs",
                     classes="helpline")

    def on_mount(self) -> None:
        t = self.query_one("#tg-table", DataTable)
        t.add_column("", width=2, key="sel")
        t.add_column("Note", width=38, key="note")
        t.add_column("Header", width=9, key="hdr")
        t.add_column("Proposed header fields", key="prop")
        t.add_column("Conf", width=5, key="conf")
        t.add_column("State", width=11, key="state")
        t.add_column("Src", width=6, key="src")
        self.query_one("#tg-route", Checkbox).value = len(self.session.taxonomy) > 0
        self.maybe_refresh(force=True)

    # ------------------------------------------------------------------ data
    def _error_rels(self) -> set[str]:
        out: set[str] = set()
        for j in self.session.jobs.jobs:
            out |= {i.key for i in j.errors}
        return out

    def _rels(self) -> list[str]:
        s = self.session
        f, q = self.filter, self.search.lower()
        thr = s.settings.auto_accept_threshold
        errs = self._error_rels() if f == "errors" else set()
        out = []
        for rel, n in s.index.notes.items():
            if q and q not in rel.lower():
                continue
            ch = s.stage.get(rel, "header")
            if f == "needs":
                ok = bool(n.missing) and ch is None
            elif f == "all":
                ok = True
            elif f in ("proposed", "accepted", "rejected"):
                ok = ch is not None and ch.status == f
            elif f == "noheader":
                ok = not n.has_fm
            elif f == "lowconf":
                ok = ch is not None and ch.status == "proposed" and ch.confidence is not None and ch.confidence < thr
            elif f == "errors":
                ok = rel in errs
            else:
                ok = s.is_locked(rel)
            if ok:
                out.append(rel)
        out.sort(key=str.lower)
        return out

    def refresh_view(self) -> None:
        s = self.session
        t = self.query_one("#tg-table", DataTable)
        keep = self.cur
        rels = self._rels()
        shown = rels[:ROW_CAP]
        t.clear()
        for rel in shown:
            n = s.index.get(rel)
            ch = s.stage.get(rel, "header")
            lock = " 🔒" if s.is_locked(rel) else ""
            t.add_row(Text("◆", style="bold magenta") if rel in self.sel else "",
                      Text(rel + lock), header_state(n),
                      short(ch.summary(), 90) if ch else Text("", style="dim"),
                      conf_text(ch.confidence) if ch else "", status_text(ch.status) if ch else "",
                      ch.source if ch else "", key=rel)
        if keep and keep in shown:
            try:
                t.move_cursor(row=t.get_row_index(keep), animate=False)
            except Exception:
                pass
        self._progress(len(rels), len(shown))

    def _progress(self, total_rows: int, shown: int) -> None:
        s = self.session
        jobs = [j for j in s.jobs.active() if "tag" in j.kind]
        line = []
        if jobs:
            j = jobs[0]
            frac = j.finished_count / j.total if j.total else 1
            eta = ""
            if j.rate > 0:
                rem = (j.total - j.finished_count) / j.rate
                eta = f" · eta {int(rem // 60):02d}:{int(rem % 60):02d}"
            state = " [b red]PAUSED[/]" if j.state == "paused" else ""
            line.append(f"job #{j.id}{state}  {progress_bar(frac)}  {j.finished_count}/{j.total}  {j.rate:.1f} notes/s"
                        f" · {len(j.errors)} error(s) · {j.tokens:,} tok{eta}")
            if len(jobs) > 1:
                line.append(f"[dim](+{len(jobs) - 1} more running — see Jobs)[/]")
        else:
            st = s.index.stats()
            line.append(f"[dim]idle · {st['incomplete']} notes need a header · showing {shown}"
                        + (f" of {total_rows}" if total_rows > shown else "") + f" · {len(self.sel)} selected[/]")
        self.query_one("#tg-progress", Static).update("  ".join(line))

    # ------------------------------------------------------------------ targets
    def targets(self) -> list[str]:
        if self.sel:
            return sorted(self.sel)
        return [self.cur] if self.cur else []

    def on_data_table_row_highlighted(self, ev: DataTable.RowHighlighted) -> None:
        self.cur = ev.row_key.value if ev.row_key is not None else None

    def on_data_table_row_selected(self, ev: DataTable.RowSelected) -> None:
        rel = ev.row_key.value
        c = self.session.stage.get(rel, "header")
        if c is None:
            self.toast("no proposal for this note yet (r = run the AI on it)")
            return
        pv = self.session.preview_change(c)
        text = Text()
        for tag, line in pv["diff"]:
            text.append(f"{tag} {line}\n", style={"+": "green", "-": "red"}.get(tag, "dim"))
        if c.reason:
            text.append(f"\nwhy: {c.reason}\n", style="italic")
        self.app.push_screen(InfoModal(f"{rel} — proposed header", text, wide=True))

    # ------------------------------------------------------------------ controls
    def on_select_changed(self, ev: Select.Changed) -> None:
        if ev.select.id == "tg-filter":
            self.filter = str(ev.value)
            self.maybe_refresh(force=True)

    def on_input_changed(self, ev: Input.Changed) -> None:
        if ev.input.id == "tg-search":
            self.search = ev.value
            self.maybe_refresh(force=True)

    def on_input_submitted(self, ev: Input.Submitted) -> None:
        s = self.session
        try:
            if ev.input.id == "tg-workers":
                s.settings.workers = int(ev.value)
                s.save_settings()
                for j in s.jobs.active():
                    j.set_workers(s.settings.workers)
                self.toast(f"workers → {s.settings.workers} (applied to running jobs too)")
            elif ev.input.id == "tg-batch":
                s.settings.batch_size = int(ev.value)
                s.save_settings()
                self.toast(f"notes per request → {s.settings.batch_size} (new jobs)")
        except ValueError:
            self.toast("enter a whole number", "error")

    def on_button_pressed(self, ev: Button.Pressed) -> None:
        {"tg-run": self.action_run_missing, "tg-rerun": self.action_rerun, "tg-acc": self.action_accept_conf}.get(
            ev.button.id, lambda: None)()

    # ------------------------------------------------------------------ actions
    def _route(self) -> bool:
        return self.query_one("#tg-route", Checkbox).value

    def action_run_missing(self) -> None:
        job = self.session.run_ai(None, header=True, route=self._route(), only_unstaged=True)
        self.toast(f"started job #{job.id}: {job.total} note(s)" if job else "nothing to do — every note already has a header or a proposal")

    def action_rerun(self) -> None:
        tg = self.targets()
        if not tg:
            return
        job = self.session.run_ai(tg, header=True, route=self._route(), fresh=True, title=f"re-run on {len(tg)} note(s)")
        self.toast(f"started job #{job.id}" if job else "nothing to do (locked / excluded / header complete)")

    def _change_ids(self) -> list[str]:
        return [c.id for rel in self.targets() if (c := self.session.stage.get(rel, "header"))]

    def action_toggle_select(self) -> None:
        if self.cur:
            self.sel.symmetric_difference_update({self.cur})
            self.maybe_refresh(force=True)
            self.query_one("#tg-table", DataTable).action_cursor_down()

    def action_accept(self) -> None:
        self.session.accept(self._change_ids())
        self._after()

    def action_reject(self) -> None:
        self.session.reject(self._change_ids())
        self._after()

    def action_unstage(self) -> None:
        for cid in self._change_ids():
            self.session.stage.remove(cid)
        self._after()

    def action_accept_conf(self) -> None:
        thr = self.session.settings.auto_accept_threshold
        n = self.session.accept_all(min_confidence=thr, kind="header")
        self.toast(f"accepted {n} header proposal(s) with confidence ≥ {thr:.2f}")

    def action_lock(self) -> None:
        for rel in self.targets():
            self.session.toggle_lock(rel)

    def _after(self) -> None:
        self.sel.clear()
        self.maybe_refresh(force=True)

    def action_edit(self) -> None:
        s = self.session
        if not self.cur or s.index.get(self.cur) is None:
            return
        rel = self.cur

        def done(res):
            if res:
                have = s.effective_values(rel)
                over = [k for k in res if any(h.lower() == k.lower() and have[h] for h in have)]
                s.stage_header(rel, res, overwrite=over, source="manual")
                self.toast("edit staged and accepted")
        self.app.push_screen(FieldsModal(f"Edit header — {rel}", s.settings.header_schema, s.effective_values(rel)), done)
