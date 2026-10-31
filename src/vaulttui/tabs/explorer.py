"""Explorer tab: browse the vault like a file explorer, see/edit headers by hand, stage moves."""
from __future__ import annotations

import os
import shlex
import subprocess

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Static, TextArea, Tree

from vaultcore import frontmatter as fm
from vaultcore.pathutil import is_under, split_rel

from ..modals import ConfirmModal, FieldsModal, FolderModal, PromptModal
from .base import TabBase, short


class NoteTree(Tree):
    """Tree whose Space key selects notes (instead of toggling folders — Enter/arrows still do that)."""
    BINDINGS = [Binding("space", "select_note", "Select")]

    def action_select_note(self) -> None:
        self.app.query_one(ExplorerTab).action_toggle_select()


class ExplorerTab(TabBase):
    TAB_ID = "explorer"
    MAIN_FOCUS = "#ex-tree"
    WATCH = {"scan", "stage", "taxonomy", "settings", "rules"}
    MIN_INTERVAL = 1.2

    BINDINGS = [
        Binding("space", "toggle_select", "Select"),
        Binding("h", "edit_header", "Edit header"),
        Binding("m", "move", "Move"),
        Binding("r", "rename", "Rename"),
        Binding("t", "tag", "AI-tag"),
        Binding("l", "lock", "Lock"),
        Binding("x", "unstage", "Unstage"),
        Binding("n", "new_folder", "New folder"),
        Binding("v", "view", "Current/Planned"),
        Binding("e", "edit_body", "Edit body"),
        Binding("ctrl+s", "save_body", "Save body"),
        Binding("o", "external", "$EDITOR"),
    ]

    def __init__(self, session, **kw):
        super().__init__(session, **kw)
        self.mode = "current"                # current | planned
        self.sel: set[str] = set()
        self.cur: dict | None = None         # data of the highlighted node
        self.expanded: set[str] = set()
        self.editing = False
        self._first = True

    # ------------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="ex-left"):
                yield Static("", id="ex-treehead", classes="pane-title")
                yield NoteTree("vault", id="ex-tree")
            with Vertical(id="ex-right"):
                yield Static("", id="ex-info", classes="pane-title")
                yield Static("", id="ex-header")
                yield TextArea("", id="ex-body", read_only=True, show_line_numbers=False, soft_wrap=True)
        yield Static("[b]space[/] select  [b]h[/] edit header  [b]m[/] move  [b]r[/] rename  [b]t[/] AI-tag  [b]l[/] lock  "
                     "[b]x[/] unstage  [b]n[/] new folder  [b]v[/] current/planned  [b]e[/] edit body  [b]o[/] $EDITOR",
                     classes="helpline")

    def on_mount(self) -> None:
        t = self.query_one("#ex-tree", Tree)
        t.show_root = False
        t.guide_depth = 3
        self.maybe_refresh(force=True)

    # ------------------------------------------------------------------ tree building
    def _folder_of(self, rel: str) -> str:
        return self.session.effective_folder(rel) if self.mode == "planned" else split_rel(rel)[0]

    def _note_label(self, rel: str) -> Text:
        s = self.session
        n = s.index.get(rel)
        t = Text()
        if rel in self.sel:
            t.append("◆ ", style="bold magenta")
        if n is None:
            return t.append(rel)
        if not n.has_fm:
            t.append("○ ", style="red")
        elif n.missing:
            t.append("◐ ", style="yellow")
        else:
            t.append("● ", style="green")
        t.append(n.title)
        marks = s.stage.for_note(rel)
        live = [c for c in marks if c.status != "rejected"]
        if live:
            t.append(" ✎" + "".join({"header": "h", "move": "m"}[c.kind] for c in live), style="cyan")
        if s.is_locked(rel):
            t.append(" [locked]", style="dim red")
        if self.mode == "planned" and n.folder != s.effective_folder(rel):
            t.append(f"  ← {n.folder or '(root)'}", style="dim")
        return t

    def refresh_view(self) -> None:
        s = self.session
        tree = self.query_one("#ex-tree", Tree)
        keep = self.cur
        tree.clear()
        notes = list(s.index.notes)
        by_folder: dict[str, list[str]] = {}
        for rel in notes:
            by_folder.setdefault(self._folder_of(rel), []).append(rel)
        folders = set()
        if self.mode == "planned":
            folders |= set(s.taxonomy.folders)
        else:
            folders |= set(s.index.folders())
        for f in list(by_folder):
            while f:
                folders.add(f)
                f = split_rel(f)[0]
        if self._first and folders:
            self.expanded = {f for f in folders if "/" not in f}
            self._first = False
        counts: dict[str, int] = {}
        for f, lst in by_folder.items():
            g = f
            while True:
                counts[g] = counts.get(g, 0) + len(lst)
                if not g:
                    break
                g = split_rel(g)[0]
        nodes = {"": tree.root}
        for f in sorted(folders, key=str.lower):
            parent = nodes.get(split_rel(f)[0], tree.root)
            label = Text.assemble((split_rel(f)[1], "bold blue"), (f"  {counts.get(f, 0)}", "dim"))
            if self.mode == "planned" and f in s.taxonomy and not s.taxonomy.folders[f].llm:
                label.append("  [no-AI]", style="dim")
            nodes[f] = parent.add(label, data={"kind": "folder", "path": f}, expand=f in self.expanded)
        for f, lst in by_folder.items():
            parent = nodes.get(f, tree.root)
            for rel in sorted(lst, key=lambda r: split_rel(r)[1].lower()):
                parent.add_leaf(self._note_label(rel), data={"kind": "note", "rel": rel})
        tree.root.expand()
        st = s.index.stats()
        self.query_one("#ex-treehead", Static).update(
            f"{'PLANNED' if self.mode == 'planned' else 'CURRENT'} view · {st['notes']} notes · "
            f"{st['incomplete']} need a header · {len(self.sel)} selected   [dim](v = switch)[/]")
        self._show_details()

    def on_tree_node_expanded(self, ev: Tree.NodeExpanded) -> None:
        d = ev.node.data
        if d and d["kind"] == "folder":
            self.expanded.add(d["path"])

    def on_tree_node_collapsed(self, ev: Tree.NodeCollapsed) -> None:
        d = ev.node.data
        if d and d["kind"] == "folder":
            self.expanded.discard(d["path"])

    def on_tree_node_highlighted(self, ev: Tree.NodeHighlighted) -> None:
        new = ev.node.data
        if self.editing and new != self.cur:
            self._stop_edit()
            self.toast("edit discarded (Ctrl+S saves)", "warning")
        self.cur = new
        self._show_details()

    # ------------------------------------------------------------------ details panel
    def _show_details(self) -> None:
        s = self.session
        info = self.query_one("#ex-info", Static)
        hdr = self.query_one("#ex-header", Static)
        body = self.query_one("#ex-body", TextArea)
        d = self.cur
        if not d:
            info.update("Select a note or folder")
            hdr.update("")
            return
        if d["kind"] == "folder":
            path = d["path"]
            n = sum(1 for r in s.index.notes if is_under(self._folder_of(r), path))
            tf = s.taxonomy.folders.get(path)
            info.update(f"[b]{path}/[/]  ·  {n} note(s)" + (f"  ·  planned folder — {tf.description}" if tf and tf.description else ""))
            hdr.update("[dim]n new sub-folder · r rename (planned folders) · t AI-tag everything in it[/]")
            if not self.editing:
                body.load_text("")
            return
        rel = d["rel"]
        note = s.index.get(rel)
        if note is None:
            return
        eff = s.effective_folder(rel)
        lines = [f"[b]{rel}[/]" + ("  [red]🔒 locked[/]" if s.is_locked(rel) else "")]
        if eff != note.folder:
            lines[0] += f"   [cyan]→ will move to {eff or '(root)'}/[/]"
        info.update("\n".join(lines))
        vals = s.effective_values(rel)
        ch = s.stage.get(rel, "header")
        staged = set(ch.data.get("fields", {})) if ch and ch.status != "rejected" else set()
        out = []
        for sp in s.settings.header_schema:
            cur = next((v for k, v in vals.items() if k.lower() == sp.key.lower()), [])
            txt = ", ".join(cur) if cur else "[dim](empty)[/]"
            mark = "  [yellow]← proposed[/]" if sp.key in staged else ""
            out.append(f"[b]{sp.title:<12}[/] {txt}{mark}")
        for k, v in vals.items():
            if not s.settings.spec(k):
                out.append(f"[dim]{k:<12} {', '.join(v)}[/]")
        for c in s.stage.for_note(rel):
            if c.status != "rejected":
                out.append(f"[cyan]{c.kind}[/] [{c.status}, {c.source}] {short(c.summary(), 70)}")
        hdr.update("\n".join(out))
        if not self.editing:
            try:
                text = s.note_text(rel)
            except (OSError, UnicodeDecodeError) as e:
                text = f"(cannot read: {e})"
            body.load_text(text[:30000])

    # ------------------------------------------------------------------ targets
    def targets(self) -> list[str]:
        if self.sel:
            return sorted(self.sel)
        d = self.cur
        if not d:
            return []
        if d["kind"] == "note":
            return [d["rel"]]
        return sorted(r for r in self.session.index.notes if is_under(self._folder_of(r), d["path"]))

    def _folder_choices(self) -> list[str]:
        s = self.session
        return sorted(set(s.taxonomy.paths()) | set(s.index.folders()))

    # ------------------------------------------------------------------ actions
    def action_toggle_select(self) -> None:
        d = self.cur
        if d and d["kind"] == "note":
            self.sel.symmetric_difference_update({d["rel"]})
            self.maybe_refresh(force=True)
            tree = self.query_one("#ex-tree", Tree)
            tree.action_cursor_down()

    def action_view(self) -> None:
        self.mode = "planned" if self.mode == "current" else "current"
        self.maybe_refresh(force=True)

    def action_edit_header(self) -> None:
        s = self.session
        tg = [r for r in self.targets() if s.index.get(r)]
        if not tg:
            return
        single = len(tg) == 1
        vals = s.effective_values(tg[0]) if single else {}
        title = f"Edit header — {tg[0]}" if single else f"Bulk edit header — {len(tg)} notes (only changed fields apply)"

        def done(res):
            if not res:
                return
            n = 0
            for rel in tg:
                have = s.effective_values(rel)
                over = [k for k in res if any(h.lower() == k.lower() and have[h] for h in have)]
                if s.stage_header(rel, res, overwrite=over, source="manual"):
                    n += 1
            self.toast(f"staged header edit on {n} note(s) — apply from the Stage tab (4)")
        self.app.push_screen(FieldsModal(title, s.settings.header_schema, vals), done)

    def action_move(self) -> None:
        s = self.session
        tg = [r for r in self.targets() if s.index.get(r)]
        if not tg:
            return
        default = s.effective_folder(tg[0])

        def done(folder):
            if folder is None:
                return
            if folder and len(s.taxonomy) and folder not in s.taxonomy:
                s.add_folder(folder, reroute=False)
            for rel in tg:
                s.stage_move(rel, folder, source="manual")
            self.toast(f"staged move of {len(tg)} note(s) → {folder or '(root)'}/ — apply from Stage (4)")
        self.app.push_screen(FolderModal(self._folder_choices(), default, f"Move {len(tg)} note(s) to…",
                                         descriptions={p: f.description for p, f in s.taxonomy.folders.items()}), done)

    def action_rename(self) -> None:
        s = self.session
        d = self.cur
        if not d:
            return
        if d["kind"] == "folder":
            path = d["path"]
            if path not in s.taxonomy:
                self.toast("Only planned folders can be renamed here (add it in the Organizer, or move notes)", "warning")
                return

            def done_f(new):
                if new and new != path:
                    try:
                        s.rename_folder(path, new)
                        self.toast(f"renamed {path} → {new} (staged moves re-targeted)")
                    except (ValueError, KeyError) as e:
                        self.toast(str(e), "error")
            self.app.push_screen(PromptModal("Rename planned folder", "Also re-targets staged moves, rules and instructions.", path), done_f)
            return
        rel = d["rel"]
        note = s.index.get(rel)
        c = s.stage.get(rel, "move")
        cur_name = (c.data.get("name") if c and c.data.get("name") else note.name)[:-3]

        def done(new):
            if new and new.strip() and new.strip() != note.title:
                s.stage_move(rel, s.effective_folder(rel), name=new.strip(), source="manual")
                self.toast("staged rename — apply from Stage (4). [[links]] by name will need updating if Obsidian doesn't do it.")
        self.app.push_screen(PromptModal("Rename note", "New file name (without .md)", cur_name), done)

    def action_tag(self) -> None:
        tg = self.targets()
        if not tg:
            return
        job = self.session.run_ai(tg, fresh=True, title=f"explorer: AI on {len(tg)} note(s)")
        self.toast(f"AI started on {len(tg)} note(s) — see Tagger (2) / Jobs (5)" if job else "nothing to do (locked / excluded?)")

    def action_lock(self) -> None:
        for rel in self.targets():
            self.session.toggle_lock(rel)
        self.maybe_refresh(force=True)

    def action_unstage(self) -> None:
        for rel in self.targets():
            self.session.unstage_note(rel)
        self.maybe_refresh(force=True)

    def action_new_folder(self) -> None:
        s = self.session
        base = (self.cur or {}).get("path", "") if (self.cur or {}).get("kind") == "folder" else ""

        def done(path):
            if path and path.strip():
                try:
                    s.add_folder(path.strip().strip("/"), reroute=False)
                    self.mode = "planned"
                    self.toast(f"planned folder added: {path} (see Organizer 3)")
                except ValueError as e:
                    self.toast(str(e), "error")
        self.app.push_screen(PromptModal("New planned folder", "Path, e.g. 3_University/Homework", (base + "/") if base else ""), done)

    # ---- body editing (rare: headers are the main thing) ----
    def _stop_edit(self) -> None:
        self.editing = False
        self.query_one("#ex-body", TextArea).read_only = True

    def action_edit_body(self) -> None:
        d = self.cur
        if not d or d["kind"] != "note":
            return
        body = self.query_one("#ex-body", TextArea)
        if self.editing:
            self._stop_edit()
            self._show_details()
            return
        self.editing = True
        body.read_only = False
        body.focus()
        self.toast("editing — Ctrl+S saves (undoable), e exits without saving")

    def action_save_body(self) -> None:
        d = self.cur
        if not self.editing or not d or d["kind"] != "note":
            return
        rel = d["rel"]
        s = self.session
        orig = s.note_text(rel)
        text = self.query_one("#ex-body", TextArea).text
        if fm.detect_newline(orig) == "\r\n":
            text = text.replace("\r\n", "\n").replace("\n", "\r\n")
        if orig.startswith("﻿") and not text.startswith("﻿"):
            text = "﻿" + text
        if text != orig:
            s.save_note(rel, text)
            self.toast("saved (Stage ▸ u undoes it)")
        self._stop_edit()
        self.query_one("#ex-tree", Tree).focus()

    def action_external(self) -> None:
        d = self.cur
        if not d or d["kind"] != "note":
            return
        rel = d["rel"]
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or ("notepad" if os.name == "nt" else "nano")
        try:
            with self.app.suspend():
                subprocess.call(shlex.split(editor) + [self.session.index.abs(rel)])
        except Exception as e:  # noqa: BLE001
            self.toast(f"could not launch editor: {e}", "error")
        self.session.index.refresh([rel])
        self.session.emit("scan", diff=None)
