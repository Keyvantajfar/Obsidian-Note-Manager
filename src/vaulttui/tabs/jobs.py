"""Jobs tab: everything the AI is (or was) doing — pause, cancel, retry, change workers live."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Input, Label, RichLog, Static

from .base import TabBase, short
from .tagger import progress_bar


class JobsTab(TabBase):
    TAB_ID = "jobs"
    MAIN_FOCUS = "#jb-table"
    WATCH = {"job", "settings"}
    MIN_INTERVAL = 0.5

    BINDINGS = [
        Binding("space", "toggle", "Pause/Resume"),
        Binding("c", "cancel", "Cancel"),
        Binding("r", "retry", "Retry failed"),
        Binding("C", "clear", "Clear finished"),
    ]

    def __init__(self, session, **kw):
        super().__init__(session, **kw)
        self.cur: int | None = None
        self._log_len = 0
        self._log_job: int | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Button("Pause / resume", id="jb-pause")
            yield Button("Cancel", id="jb-cancel", variant="error")
            yield Button("Retry failed", id="jb-retry")
            yield Button("Clear finished", id="jb-clear")
            yield Label("workers")
            yield Input(str(self.session.settings.workers), id="jb-workers", classes="tiny")
        yield DataTable(id="jb-table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="jb-detail", classes="pane-title")
        yield RichLog(id="jb-log", wrap=True, markup=False, max_lines=500)
        yield Static("[b]space[/] pause/resume  [b]c[/] cancel  [b]r[/] retry failed items  [b]C[/] clear finished  [b]p[/] pause all  "
                     "·  changing workers applies to the selected job immediately", classes="helpline")

    def on_mount(self) -> None:
        t = self.query_one("#jb-table", DataTable)
        t.add_column("#", width=4, key="id")
        t.add_column("Job", width=44, key="title")
        t.add_column("State", width=10, key="state")
        t.add_column("Progress", width=40, key="prog")
        t.add_column("Rate", width=8, key="rate")
        t.add_column("Err", width=4, key="err")
        t.add_column("Workers", width=7, key="w")
        t.add_column("Tokens", width=9, key="tok")
        self.maybe_refresh(force=True)

    def _job(self):
        return self.session.jobs.get(self.cur) if self.cur is not None else None

    def refresh_view(self) -> None:
        t = self.query_one("#jb-table", DataTable)
        keep = self.cur
        t.clear()
        for j in reversed(self.session.jobs.jobs):
            frac = j.finished_count / j.total if j.total else 1
            st = {"done": Text("done", style="green"), "cancelled": Text("cancelled", style="dim"),
                  "paused": Text("paused", style="bold yellow"), "running": Text("running", style="bold cyan")}.get(j.state, Text(j.state))
            t.add_row(str(j.id), short(j.title, 44), st, f"{progress_bar(frac, 22)} {j.finished_count}/{j.total}",
                      f"{j.rate:.1f}/s", Text(str(len(j.errors)), style="red" if j.errors else "dim"), str(j.workers), f"{j.tokens:,}", key=str(j.id))
        if keep is not None:
            try:
                t.move_cursor(row=t.get_row_index(str(keep)), animate=False)
            except Exception:
                pass
        self._detail()

    def _detail(self) -> None:
        j = self._job()
        det = self.query_one("#jb-detail", Static)
        log = self.query_one("#jb-log", RichLog)
        if j is None:
            det.update("Select a job")
            return
        det.update(f"#{j.id} {j.kind} · {j.state} · {j.count('done')} done · {j.count('error')} failed · "
                   f"{j.count('skipped', 'stale')} skipped · {j.count('queued', 'running')} pending")
        if self._log_job != j.id:
            log.clear()
            self._log_job, self._log_len = j.id, 0
        lines = list(j.log)
        if len(lines) < self._log_len:
            log.clear()
            self._log_len = 0
        for line in lines[self._log_len:]:
            log.write(line)
        self._log_len = len(lines)

    def on_data_table_row_highlighted(self, ev: DataTable.RowHighlighted) -> None:
        self.cur = int(ev.row_key.value) if ev.row_key is not None and ev.row_key.value else None
        j = self._job()
        if j is not None:
            self.query_one("#jb-workers", Input).value = str(j.workers)
        self._detail()

    def on_input_submitted(self, ev: Input.Submitted) -> None:
        if ev.input.id != "jb-workers":
            return
        try:
            n = int(ev.value)
        except ValueError:
            self.toast("enter a whole number", "error")
            return
        j = self._job()
        if j is not None and j.active:
            j.set_workers(n)
        self.session.settings.workers = max(1, n)
        self.session.save_settings()
        self.toast(f"workers → {max(1, n)}")

    def on_button_pressed(self, ev: Button.Pressed) -> None:
        {"jb-pause": self.action_toggle, "jb-cancel": self.action_cancel, "jb-retry": self.action_retry,
         "jb-clear": self.action_clear}.get(ev.button.id, lambda: None)()

    def action_toggle(self) -> None:
        j = self._job()
        if j is None:
            return
        j.resume() if j.state == "paused" else j.pause()

    def action_cancel(self) -> None:
        j = self._job()
        if j is not None:
            j.cancel()

    def action_clear(self) -> None:
        self.session.jobs.clear_finished()
        self.cur = None
        self.maybe_refresh(force=True)

    def action_retry(self) -> None:
        j = self._job()
        if j is None or not j.errors:
            self.toast("no failed items in this job")
            return
        rels = [i.key for i in j.errors]
        job = self.session.run_ai(rels, header="tag" in j.kind, route="route" in j.kind, fresh=True,
                                  title=f"retry of #{j.id}: {len(rels)} note(s)")
        self.toast(f"retrying {len(rels)} note(s) as job #{job.id}" if job else "nothing to retry")
