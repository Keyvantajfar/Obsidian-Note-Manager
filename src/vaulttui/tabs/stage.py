"""Stage tab: every pending change in one place — review, edit paths/names/fields, dry-run, apply, undo."""
from __future__ import annotations

import time

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Select, Static

from vaultcore.pathutil import split_rel

from ..modals import ApplyModal, ConfirmModal, FieldsModal, FolderModal, InfoModal, PromptModal
from .base import TabBase, conf_text, short, status_text

FILTERS = [
    ("All changes", "all"), ("Proposed", "proposed"), ("Accepted", "accepted"), ("Rejected", "rejected"),
    ("Headers", "header"), ("Moves", "move"), ("Low confidence", "lowconf"), ("AI", "ai"), ("Rules", "rule"), ("Manual", "manual"),
]
ROW_CAP = 3000


class StageTab(TabBase):
    TAB_ID = "stage"
    MAIN_FOCUS = "#st-table"
    WATCH = {"stage", "scan", "applied", "undone"}
    MIN_INTERVAL = 0.8

    BINDINGS = [
        Binding("space", "cycle", "Accept/Reject"),
        Binding("a", "accept", "Accept"),
        Binding("x", "reject", "Reject"),
        Binding("e", "edit", "Edit"),
        Binding("d", "delete", "Remove"),
        Binding("A", "accept_all", "Accept all proposed"),
        Binding("T", "accept_conf", "Accept ≥ thr"),
        Binding("P", "apply", "Dry-run & Apply"),
        Binding("u", "undo", "Undo last apply"),
        Binding("c", "clear_rejected", "Clear rejected"),
    ]

    def __init__(self, session, **kw):
        super().__init__(session, **kw)
        self.filter = "all"
        self.cur: str | None = None
        self.sel: set[str] = set()

    def compose(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Select(FILTERS, value="all", allow_blank=False, id="st-filter")
            yield Button("Accept all proposed", id="st-accall")
            yield Button("Accept ≥ thr", id="st-accconf")
            yield Button("Reject", id="st-rej")
            yield Button("Dry-run & Apply ▶", variant="success", id="st-apply")
            yield Button("Undo last", id="st-undo")
        yield Static("", id="st-summary")
        with Horizontal(id="st-main"):
            yield DataTable(id="st-table", cursor_type="row", zebra_stripes=True)
            yield Static("", id="st-preview")
        yield Static("[b]space[/] cycle accept/reject  [b]a[/]/[b]x[/]  [b]e[/] edit destination / name / fields  [b]d[/] remove  "
                     "[b]A[/] accept all  [b]T[/] accept ≥ thr  [b]P[/] dry-run & apply  [b]u[/] undo last batch  [b]c[/] clear rejected",
                     classes="helpline")

    def on_mount(self) -> None:
        t = self.query_one("#st-table", DataTable)
        t.add_column("", width=2, key="sel")
        t.add_column("State", width=11, key="state")
        t.add_column("Kind", width=6, key="kind")
        t.add_column("Src", width=6, key="src")
        t.add_column("Conf", width=5, key="conf")
        t.add_column("Note", width=36, key="note")
        t.add_column("Change", key="chg")
        self.maybe_refresh(force=True)

    # ------------------------------------------------------------------ data
    def _changes(self):
        s = self.session
        f = self.filter
        thr = s.settings.auto_accept_threshold
        out = []
        for c in s.stage.all():
            if f in ("proposed", "accepted", "rejected"):
                ok = c.status == f
            elif f in ("header", "move"):
                ok = c.kind == f
            elif f == "lowconf":
                ok = c.confidence is not None and c.confidence < thr and c.status != "rejected"
            elif f in ("ai", "rule", "manual"):
                ok = c.source == f
            else:
                ok = True
            if ok:
                out.append(c)
        return out

    def refresh_view(self) -> None:
        s = self.session
        t = self.query_one("#st-table", DataTable)
        keep = self.cur
        chs = self._changes()[:ROW_CAP]
        t.clear()
        for c in chs:
            t.add_row(Text("◆", style="bold magenta") if c.id in self.sel else "", status_text(c.status), c.kind, c.source,
                      conf_text(c.confidence), c.note, short(c.summary(), 100), key=c.id)
        if keep:
            try:
                t.move_cursor(row=t.get_row_index(keep), animate=False)
            except Exception:
                pass
        cn = s.stage.counts()
        b = s.batches()
        last = next((x for x in reversed(b) if not x.undone), None)
        self.query_one("#st-summary", Static).update(
            f"[yellow]● {cn['proposed']} proposed[/]   [green]✔ {cn['accepted']} accepted[/]   [red]✘ {cn['rejected']} rejected[/]"
            f"   ·   last applied batch: {last.label + ' · ' + time.strftime('%H:%M:%S', time.localtime(last.time)) + f' · {last.ops} op(s)' if last else '—'}"
            f"   [dim]· nothing touches your vault until you press Apply[/]")
        self._preview()

    def _preview(self) -> None:
        pv = self.query_one("#st-preview", Static)
        c = self.session.stage.changes.get(self.cur) if self.cur else None
        if c is None:
            pv.update("[dim]Select a change to preview it.[/]")
            return
        p = self.session.preview_change(c)
        text = Text()
        text.append(f"{c.note}\n", style="bold")
        text.append(f"{c.kind} · {c.source} · {c.status}" + (f" · confidence {c.confidence:.2f}" if c.confidence is not None else "") + "\n\n", style="dim")
        for tag, line in p["diff"]:
            text.append(f"{tag} {line}\n", style={"+": "green", "-": "red"}.get(tag, "dim"))
        for line in p["lines"]:
            text.append(line + "\n", style="cyan")
        if c.reason:
            text.append(f"\nwhy: {c.reason}\n", style="italic")
        for w in p["warnings"]:
            text.append(f"\n! {w}", style="yellow")
        pv.update(text)

    def on_data_table_row_highlighted(self, ev: DataTable.RowHighlighted) -> None:
        self.cur = ev.row_key.value if ev.row_key is not None else None
        self._preview()

    # ------------------------------------------------------------------ helpers
    def _ids(self) -> list[str]:
        return sorted(self.sel) if self.sel else ([self.cur] if self.cur else [])

    def _after(self) -> None:
        self.sel.clear()
        self.maybe_refresh(force=True)

    def on_select_changed(self, ev: Select.Changed) -> None:
        if ev.select.id == "st-filter":
            self.filter = str(ev.value)
            self.maybe_refresh(force=True)

    def on_button_pressed(self, ev: Button.Pressed) -> None:
        {"st-accall": self.action_accept_all, "st-accconf": self.action_accept_conf, "st-rej": self.action_reject,
         "st-apply": self.action_apply, "st-undo": self.action_undo}.get(ev.button.id, lambda: None)()

    # ------------------------------------------------------------------ actions
    def action_cycle(self) -> None:
        c = self.session.stage.changes.get(self.cur) if self.cur else None
        if c is None:
            return
        nxt = {"proposed": "accepted", "accepted": "rejected", "rejected": "proposed"}[c.status]
        self.session.stage.set_status(c.id, nxt)
        self.query_one("#st-table", DataTable).action_cursor_down()

    def action_accept(self) -> None:
        self.session.accept(self._ids())
        self._after()

    def action_reject(self) -> None:
        self.session.reject(self._ids())
        self._after()

    def action_delete(self) -> None:
        for cid in self._ids():
            self.session.stage.remove(cid)
        self._after()

    def action_clear_rejected(self) -> None:
        n = self.session.stage.remove_where(lambda c: c.status == "rejected")
        self.toast(f"removed {n} rejected change(s)")

    def action_accept_all(self) -> None:
        n = self.session.accept_all()
        self.toast(f"accepted {n} change(s)")

    def action_accept_conf(self) -> None:
        thr = self.session.settings.auto_accept_threshold
        n = self.session.accept_all(min_confidence=thr)
        self.toast(f"accepted {n} change(s) with confidence ≥ {thr:.2f}")

    def action_edit(self) -> None:
        s = self.session
        c = s.stage.changes.get(self.cur) if self.cur else None
        if c is None:
            return
        note = s.index.get(c.note)
        if note is None:
            return
        if c.kind == "header":
            vals = s.effective_values(c.note)

            def done(res):
                if res:
                    over = [k for k in res if any(h.lower() == k.lower() and vals[h] for h in vals)]
                    s.stage_header(c.note, res, overwrite=over, source="manual")
            self.app.push_screen(FieldsModal(f"Edit header — {c.note}", s.settings.header_schema, vals), done)
            return
        folders = sorted(set(s.taxonomy.paths()) | set(s.index.folders()))

        def got_folder(folder):
            if folder is None:
                return
            if folder and len(s.taxonomy) and folder not in s.taxonomy:
                s.add_folder(folder, reroute=False)

            def got_name(name):
                if name is None:
                    return
                name = name.strip()
                same = name == (c.data.get("name") or note.name)[:-3] or name == note.title
                s.stage_move(c.note, folder, name=None if same else name, source="manual")
            self.app.push_screen(PromptModal("File name", "Keep or change the name (without .md)",
                                             (c.data.get("name") or note.name)[:-3] if (c.data.get("name") or note.name).endswith(".md") else note.title), got_name)
        self.app.push_screen(FolderModal(folders, c.data.get("folder", ""), f"Destination for {note.name}",
                                         descriptions={p: f.description for p, f in s.taxonomy.folders.items()}), got_folder)

    # ---- apply / undo
    def action_apply(self) -> None:
        s = self.session
        if not s.stage.all(status="accepted"):
            self.toast("nothing accepted yet — accept changes first (a / A / T)", "warning")
            return
        plan = s.plan_apply()

        def go(ok):
            if not ok:
                return
            res = s.apply(plan)
            lines = [f"Applied {len(res.applied)} change(s)." + (f"  Batch [b]{res.batch_id}[/] — press [b]u[/] to undo it." if res.batch_id else ""),
                     f"{len(res.written)} header(s) written, {len(res.moved)} note(s) moved, {len(res.removed_dirs)} empty folder(s) removed."]
            if res.failed:
                lines.append("\n[red]Failed:[/]")
                lines += [f"  {cid}: {msg}" for cid, msg in res.failed]
            self.app.push_screen(InfoModal("Done", "\n".join(lines)))
        self.app.push_screen(ApplyModal(plan), go)

    def action_undo(self) -> None:
        s = self.session
        last = next((b for b in reversed(s.batches()) if not b.undone), None)
        if last is None:
            self.toast("nothing to undo")
            return

        def go(ok):
            if not ok:
                return
            r = s.undo(last.id)
            msg = f"Restored {r.restored} header(s), moved back {r.moved_back} note(s)."
            if r.conflicts:
                msg += "\n\n[yellow]Left alone:[/]\n" + "\n".join(f"  {c}" for c in r.conflicts)
            self.app.push_screen(InfoModal("Undo", msg))
        self.app.push_screen(ConfirmModal(
            f"Undo batch [b]{last.id}[/] ({last.label}, {last.ops} operation(s), "
            f"{time.strftime('%H:%M:%S', time.localtime(last.time))})?\n\nNotes edited since are left untouched and reported.",
            "Undo", "Cancel"), go)
