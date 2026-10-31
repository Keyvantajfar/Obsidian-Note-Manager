"""Organizer tab: your target folder structure ("plan"). Edit it by hand, let the AI propose / route,
triage the notes it couldn't place. Moves are staged — nothing is written until you Apply."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Label, OptionList, Static, Switch, Tree
from textual.widgets.option_list import Option

from vaultcore.pathutil import is_under, split_rel

from ..modals import ConfirmModal, FolderModal, PromptModal, StructureModal
from .base import TabBase, conf_text, short, status_text


class OrganizerTab(TabBase):
    TAB_ID = "organizer"
    MAIN_FOCUS = "#og-plantree"
    WATCH = {"scan", "stage", "taxonomy", "job", "rules"}
    MIN_INTERVAL = 1.0

    BINDINGS = [
        Binding("n", "new_folder", "New folder"),
        Binding("R", "rename", "Rename folder"),
        Binding("d", "delete", "Delete folder"),
        Binding("m", "move", "Move note(s)"),
        Binding("p", "pin", "Pin here"),
        Binding("space", "toggle_select", "Select"),
        Binding("g", "route_all", "Route all (AI)"),
        Binding("t", "route_selected", "Route this (AI)"),
        Binding("f", "freeze", "Freeze plan"),
        Binding("y", "accept_suggestion", "Accept suggestion"),
        Binding("X", "dismiss_suggestion", "Dismiss suggestion"),
        Binding("i", "import_folder", "Add current folder to plan"),
    ]

    def __init__(self, session, **kw):
        super().__init__(session, **kw)
        self.cur_path: str | None = None
        self.cur_note: str | None = None
        self.cur_triage: str | None = None
        self.cur_sugg: str | None = None
        self.cur_real: str | None = None
        self.sel: set[str] = set()
        self.expanded: set[str] = set()
        self._populating = False
        self._first = True

    # ------------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Button("Propose structure (AI)", variant="primary", id="og-propose")
            yield Button("Import current folders", id="og-import")
            yield Button("Route notes (AI)", id="og-route")
            yield Button("New folder", id="og-new")
            yield Button("Freeze plan: off", id="og-freeze")
            yield Input(placeholder="optional preferences for the AI, e.g. keep ML notes under 2_Research/AI", id="og-prefs")
        with Horizontal(id="og-main"):
            with Vertical(id="og-cur"):
                yield Static("CURRENT folders  [dim](i = add to plan)[/]", classes="pane-title")
                yield Tree("vault", id="og-current")
            with Vertical(id="og-plan"):
                yield Static("PLANNED structure", id="og-plantitle", classes="pane-title")
                yield Tree("plan", id="og-plantree")
                yield Static("AI-suggested new folders  [dim](y accept · X dismiss)[/]", classes="pane-title")
                yield OptionList(id="og-sugg")
            with Vertical(id="og-detail"):
                yield Static("", id="og-dinfo", classes="pane-title")
                yield Label("Folder path (Enter renames — staged moves, rules, instructions follow)")
                yield Input(id="og-name")
                yield Label("What belongs here (Enter saves — the AI routes by this description)")
                yield Input(id="og-desc")
                with Horizontal(classes="switchrow"):
                    yield Switch(value=True, id="og-ai")
                    yield Label("AI may route notes here")
                yield Static("", id="og-rules")
                yield DataTable(id="og-notes", cursor_type="row", zebra_stripes=True)
        yield Static("NEEDS YOU — notes the AI couldn't place confidently  [dim](m assign · t re-route this one)[/]", classes="pane-title")
        yield DataTable(id="og-triage", cursor_type="row", zebra_stripes=True)
        yield Static("[b]n[/] new folder  [b]R[/] rename  [b]d[/] delete  [b]m[/] move note(s)  [b]p[/] pin here  [b]g[/] route all  "
                     "[b]f[/] freeze  [b]y[/]/[b]X[/] suggestion  [b]space[/] select", classes="helpline")

    def on_mount(self) -> None:
        for tid in ("og-current", "og-plantree"):
            t = self.query_one(f"#{tid}", Tree)
            t.show_root = False
        n = self.query_one("#og-notes", DataTable)
        n.add_column("", width=2, key="sel")
        n.add_column("Note", width=34, key="note")
        n.add_column("Now in", width=22, key="from")
        n.add_column("Src", width=6, key="src")
        n.add_column("Conf", width=5, key="conf")
        n.add_column("State", width=11, key="state")
        t = self.query_one("#og-triage", DataTable)
        t.add_column("Note", width=44, key="note")
        t.add_column("Why", key="why")
        self.maybe_refresh(force=True)

    # ------------------------------------------------------------------ refresh
    def refresh_view(self) -> None:
        s = self.session
        counts = s.plan_counts()
        # ---- current folders
        cur = self.query_one("#og-current", Tree)
        cur.clear()
        by_f: dict[str, int] = {}
        for n in s.index.notes.values():
            f = n.folder
            while True:
                by_f[f] = by_f.get(f, 0) + 1
                if not f:
                    break
                f = split_rel(f)[0]
        nodes = {"": cur.root}
        for f in sorted(s.index.folders(), key=str.lower):
            parent = nodes.get(split_rel(f)[0], cur.root)
            mark = " ✓" if f in s.taxonomy else ""
            nodes[f] = parent.add(Text.assemble((split_rel(f)[1], "blue"), (f"  {by_f.get(f, 0)}", "dim"), (mark, "green")),
                                  data=f, expand=("/" not in f))
        if by_f.get("", 0):
            cur.root.add_leaf(Text(f"(vault root)  {by_f['']}", style="dim"), data="")
        cur.root.expand()
        # ---- planned tree
        pt = self.query_one("#og-plantree", Tree)
        pt.clear()
        paths = s.taxonomy.paths()
        if self._first and paths:
            self.expanded = {p for p in paths if "/" not in p}
            self._first = False
        totals: dict[str, int] = {}
        for p, n in counts.items():
            q = p
            while q:
                totals[q] = totals.get(q, 0) + n
                q = split_rel(q)[0]
        pnodes = {"": pt.root}
        for p in paths:
            f = s.taxonomy.folders[p]
            parent = pnodes.get(split_rel(p)[0], pt.root)
            label = Text.assemble((split_rel(p)[1], "bold green"), (f"  {counts.get(p, 0)}", "bold"))
            if s.taxonomy.children(p):
                label.append(f"  Σ{totals.get(p, 0)}", style="dim")
            if not f.llm:
                label.append("  [no-AI]", style="dim red")
            pnodes[p] = parent.add(label, data=p, expand=p in self.expanded)
        pt.root.expand()
        un = len(s.unassigned())
        self.query_one("#og-plantitle", Static).update(
            f"PLANNED structure · {len(paths)} folder(s)" + ("  [b yellow]FROZEN[/]" if s.taxonomy.frozen else "")
            + (f" · {un} note(s) outside the plan" if paths and un else "")
            + ("   [dim](n to add, or 'Propose structure')[/]" if not paths else ""))
        self.query_one("#og-freeze", Button).label = f"Freeze plan: {'ON' if s.taxonomy.frozen else 'off'}"
        # ---- suggestions
        ol = self.query_one("#og-sugg", OptionList)
        ol.clear_options()
        for sp, sg in s.taxonomy.suggestions.items():
            ol.add_option(Option(f"★ {sp}  ({len(sg.notes)} note(s)){'  — ' + short(sg.reason, 40) if sg.reason else ''}", id=sp))
        # ---- triage
        tt = self.query_one("#og-triage", DataTable)
        keep = self.cur_triage
        tt.clear()
        for rel, why in s.needs_attention()[:1500]:
            tt.add_row(rel, why, key=rel)
        if keep:
            try:
                tt.move_cursor(row=tt.get_row_index(keep), animate=False)
            except Exception:
                pass
        self._detail()

    def _detail(self) -> None:
        s = self.session
        p = self.cur_path
        info = self.query_one("#og-dinfo", Static)
        nt = self.query_one("#og-notes", DataTable)
        nt.clear()
        self._populating = True
        try:
            if not p or p not in s.taxonomy:
                info.update("Select a planned folder")
                self.query_one("#og-name", Input).value = ""
                self.query_one("#og-desc", Input).value = ""
                self.query_one("#og-rules", Static).update("")
                return
            f = s.taxonomy.folders[p]
            self.query_one("#og-name", Input).value = p
            self.query_one("#og-desc", Input).value = f.description
            self.query_one("#og-ai", Switch).value = f.llm
            rules = [r.text for r in s.rules.rules if not r.error and r.move_target() == p]
            self.query_one("#og-rules", Static).update(
                "Rules routing here: " + ("\n  ".join([""] + [short(r, 90) for r in rules]) if rules else "[dim]none[/]"))
            rels = sorted((r for r in s.index.notes if s.effective_folder(r) == p), key=str.lower)
            info.update(f"[b]{p}/[/]  ·  {len(rels)} note(s) assigned")
            for rel in rels[:1500]:
                c = s.stage.get(rel, "move")
                n = s.index.get(rel)
                nt.add_row(Text("◆", style="bold magenta") if rel in self.sel else "", rel[len(p) + 1:] if rel.startswith(p + "/") else split_rel(rel)[1],
                           n.folder or "(root)", c.source if c else "stays", conf_text(c.confidence) if c else "", status_text(c.status) if c else "", key=rel)
        finally:
            self._populating = False

    # ------------------------------------------------------------------ events
    def on_tree_node_highlighted(self, ev: Tree.NodeHighlighted) -> None:
        tid = ev.node.tree.id
        if tid == "og-plantree":
            self.cur_path = ev.node.data
            self._detail()
        elif tid == "og-current":
            self.cur_real = ev.node.data

    def on_tree_node_expanded(self, ev: Tree.NodeExpanded) -> None:
        if ev.node.tree.id == "og-plantree" and ev.node.data:
            self.expanded.add(ev.node.data)

    def on_tree_node_collapsed(self, ev: Tree.NodeCollapsed) -> None:
        if ev.node.tree.id == "og-plantree" and ev.node.data:
            self.expanded.discard(ev.node.data)

    def on_data_table_row_highlighted(self, ev: DataTable.RowHighlighted) -> None:
        key = ev.row_key.value if ev.row_key is not None else None
        if ev.data_table.id == "og-notes":
            self.cur_note = key
        elif ev.data_table.id == "og-triage":
            self.cur_triage = key

    def on_data_table_row_selected(self, ev: DataTable.RowSelected) -> None:
        if ev.data_table.id == "og-triage":
            self.action_move()

    def on_option_list_option_highlighted(self, ev: OptionList.OptionHighlighted) -> None:
        self.cur_sugg = ev.option.id if ev.option is not None else None

    def on_input_submitted(self, ev: Input.Submitted) -> None:
        s = self.session
        p = self.cur_path
        if ev.input.id == "og-name" and p and ev.value.strip() and ev.value.strip() != p:
            try:
                s.rename_folder(p, ev.value.strip())
                self.cur_path = ev.value.strip().strip("/")
                self.toast(f"renamed → {self.cur_path} (staged moves, rules and instructions re-targeted)")
            except (ValueError, KeyError) as e:
                self.toast(str(e), "error")
        elif ev.input.id == "og-desc" and p:
            s.taxonomy.set_description(p, ev.value)
            s.taxonomy.save()
            s.emit("taxonomy")
            self.toast("description saved — it will be used for routing (re-run routing to apply)")

    def on_switch_changed(self, ev: Switch.Changed) -> None:
        if ev.switch.id == "og-ai" and not self._populating and self.cur_path:
            self.session.taxonomy.set_llm(self.cur_path, ev.value)
            self.session.taxonomy.save()
            self.session.emit("taxonomy")

    def on_button_pressed(self, ev: Button.Pressed) -> None:
        {"og-propose": self.action_propose, "og-import": self.action_import_all, "og-route": self.action_route_all,
         "og-new": self.action_new_folder, "og-freeze": self.action_freeze}.get(ev.button.id, lambda: None)()

    # ------------------------------------------------------------------ actions
    def _focused_id(self) -> str:
        f = self.app.focused
        return f.id if f is not None and f.id else ""

    def action_toggle_select(self) -> None:
        if self._focused_id() == "og-notes" and self.cur_note:
            self.sel.symmetric_difference_update({self.cur_note})
            self._detail()
            self.query_one("#og-notes", DataTable).action_cursor_down()

    def action_new_folder(self) -> None:
        s = self.session
        base = (self.cur_path + "/") if self.cur_path else ""

        def done(path):
            if path and path.strip().strip("/"):
                try:
                    job = s.add_folder(path.strip().strip("/"), reroute=True)
                    self.cur_path = s.taxonomy.match(path.strip().strip("/")) or self.cur_path
                    self.toast(f"folder added" + (f" — re-routing {job.total} candidate note(s) (job #{job.id})" if job else ""))
                except ValueError as e:
                    self.toast(str(e), "error")
        self.app.push_screen(PromptModal("New planned folder",
                                         "Path (nested with /). In auto/assist mode, notes that could belong there are re-routed.", base), done)

    def action_rename(self) -> None:
        if self.cur_path:
            self.query_one("#og-name", Input).focus()

    def action_delete(self) -> None:
        s = self.session
        p = self.cur_path
        if not p or p not in s.taxonomy:
            return

        def done(ok):
            if ok:
                s.remove_folder(p)
                self.cur_path = None
        self.app.push_screen(ConfirmModal(f"Remove planned folder [b]{p}[/] from the plan?\nStaged AI/rule moves into it are dropped; "
                                          f"your manual ones stay. Nothing on disk changes.", "Remove", "Cancel", danger=True), done)

    def _move_targets(self) -> list[str]:
        fid = self._focused_id()
        if fid == "og-triage":
            return [self.cur_triage] if self.cur_triage else []
        if self.sel:
            return sorted(self.sel)
        return [self.cur_note] if self.cur_note else []

    def action_move(self) -> None:
        s = self.session
        tg = [t for t in self._move_targets() if s.index.get(t)]
        if not tg:
            return

        def done(folder):
            if folder is None:
                return
            if folder and folder not in s.taxonomy:
                s.add_folder(folder, reroute=False)
            for rel in tg:
                s.stage_move(rel, folder, source="manual")
            self.sel.clear()
            self.toast(f"{len(tg)} note(s) → {folder or '(root)'}/ staged (manual wins over AI/rules)")
        self.app.push_screen(FolderModal(s.taxonomy.paths() or s.index.folders(), s.effective_folder(tg[0]), f"Move {len(tg)} note(s) to…",
                                         descriptions={p: f.description for p, f in s.taxonomy.folders.items()}), done)

    def action_pin(self) -> None:
        if not self.cur_path:
            return
        tg = self._move_targets()
        for rel in tg:
            self.session.stage_move(rel, self.cur_path, source="manual")
        self.sel.clear()
        self.toast(f"pinned {len(tg)} note(s) to {self.cur_path} (the AI won't re-route them)")

    def action_route_all(self) -> None:
        s = self.session
        if not len(s.taxonomy):
            self.toast("no planned folders yet — propose or add some first", "warning")
            return
        job = s.run_ai(None, header=False, route=True, only_unstaged=True, title="route notes into the plan")
        self.toast(f"routing {job.total} note(s) — job #{job.id}" if job else "nothing to route")

    def action_route_selected(self) -> None:
        tg = self._move_targets()
        if not tg:
            return
        job = self.session.run_ai(tg, header=False, route=True, fresh=True, title=f"re-route {len(tg)} note(s)")
        self.toast(f"re-routing job #{job.id}" if job else "nothing to do")

    def action_freeze(self) -> None:
        s = self.session
        s.set_frozen(not s.taxonomy.frozen)
        self.toast("plan frozen — the AI can only suggest extra folders" if s.taxonomy.frozen else "plan unfrozen")

    def action_accept_suggestion(self) -> None:
        if self.cur_sugg:
            n = self.session.accept_suggestion(self.cur_sugg)
            self.toast(f"folder added; {n} note(s) staged into it (as proposals)")

    def action_dismiss_suggestion(self) -> None:
        if self.cur_sugg:
            self.session.dismiss_suggestion(self.cur_sugg)

    def action_import_folder(self) -> None:
        if self.cur_real:
            self.session.add_folder(self.cur_real, reroute=False)
            self.toast(f"added {self.cur_real} to the plan")

    def action_import_all(self) -> None:
        n = self.session.import_current_folders()
        self.toast(f"added {n} current folder(s) to the plan — edit names/descriptions, then route")

    # ---- AI structure proposal
    def action_propose(self) -> None:
        s = self.session
        if s.taxonomy.frozen:
            self.toast("the plan is frozen — unfreeze it (f) before asking for a new structure", "warning")
            return
        prefs = self.query_one("#og-prefs", Input).value.strip()
        self.toast("asking the model for a structure…")
        self.run_worker(self._propose(prefs, ""), exclusive=True, group="propose")

    async def _propose(self, prefs: str, feedback: str) -> None:
        s = self.session
        try:
            folders, note = await s.propose_structure(prefs, feedback)
        except Exception as e:  # noqa: BLE001
            self.toast(f"structure proposal failed: {e}", "error")
            return
        if not folders:
            self.toast("the model returned no usable structure" + (f": {note}" if note else ""), "warning")
            return

        def done(res):
            if not res:
                return
            kind, text = res
            if kind == "feedback":
                self.run_worker(self._propose(prefs, text), exclusive=True, group="propose")
            else:
                s.adopt_structure(folders, replace=(kind == "replace"))
                self.toast(f"plan {'replaced' if kind == 'replace' else 'extended'} with {len(folders)} folder(s) — now 'Route notes (AI)'")
        self.app.push_screen(StructureModal(folders, note), done)
