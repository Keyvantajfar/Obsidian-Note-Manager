"""Rules tab: deterministic rules and free-text instructions — how you steer the AI."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Input, Static

from ..modals import InfoModal, PromptModal
from .base import TabBase, short

CHEAT = (
    "[b]Rule[/]  conditions -> actions     e.g.  [cyan]tag:ML -> move:2_Research/AI[/]   [cyan]title~homework -> move:3_Uni/Homework[/]   "
    "[cyan]path:Daily/** -> skip[/]   [cyan]noheader text~\"#recipe\" -> set!:status=Reference tag+:Cooking[/]\n"
    "  conditions: path: folder: name: (globs) · title: title~ text~ (regex) · tag: · <key>:value · noheader hasheader missing:key · prefix ! negates\n"
    "  actions: move:FOLDER · set:KEY=V (fill) · set!:KEY=V (overwrite) · add:KEY=a,b · tag+:a,b · skip (no AI) · lock (hands off)\n"
    "[b]Instruction[/]  any other sentence, e.g. [cyan]keep calculus under 3_University/Math[/]   scoped:  [cyan]@Folder/Path: …[/]   [cyan]#tag: …[/]"
)


class RulesTab(TabBase):
    TAB_ID = "rules"
    MAIN_FOCUS = "#ru-rules"
    WATCH = {"rules", "scan", "taxonomy"}
    MIN_INTERVAL = 0.8

    BINDINGS = [
        Binding("space", "toggle", "On/off"),
        Binding("d", "delete", "Delete"),
        Binding("e", "edit", "Edit"),
        Binding("bracketleft", "up", "Move up", key_display="["),
        Binding("bracketright", "down", "Move down", key_display="]"),
        Binding("c", "count", "Show matches"),
        Binding("a", "approve", "Approve suggested rule"),
        Binding("r", "rerun", "Re-run AI for instruction"),
    ]

    def __init__(self, session, **kw):
        super().__init__(session, **kw)
        self.cur_rule: str | None = None
        self.cur_ins: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="ru-main"):
            with Vertical():
                yield Static("RULES — applied instantly, no AI · top = highest priority", classes="pane-title")
                yield DataTable(id="ru-rules", cursor_type="row", zebra_stripes=True)
            with Vertical():
                yield Static("INSTRUCTIONS for the AI — plain language, used in its prompts", classes="pane-title")
                yield DataTable(id="ru-ins", cursor_type="row", zebra_stripes=True)
        yield Input(placeholder="type a rule (cond -> action) or an instruction, then Enter", id="ru-new")
        yield Static(CHEAT, id="ru-cheat")
        yield Static("[b]space[/] on/off  [b]d[/] delete  [b]e[/] edit  [b]\\[ \\][/] reorder  [b]c[/] show matching notes  "
                     "[b]a[/] approve an AI-suggested rule  [b]r[/] re-run AI for an instruction", classes="helpline")

    def on_mount(self) -> None:
        r = self.query_one("#ru-rules", DataTable)
        r.add_column("On", width=3, key="on")
        r.add_column("Rule", key="text")
        r.add_column("State", width=18, key="st")
        i = self.query_one("#ru-ins", DataTable)
        i.add_column("On", width=3, key="on")
        i.add_column("Applies to", width=22, key="scope")
        i.add_column("Task", width=6, key="task")
        i.add_column("Instruction", key="text")
        self.maybe_refresh(force=True)

    def refresh_view(self) -> None:
        s = self.session
        r = self.query_one("#ru-rules", DataTable)
        keep = self.cur_rule
        r.clear()
        for rule in s.rules.rules:
            on = Text("✔", style="green") if rule.enabled and not rule.error else Text("·", style="dim")
            st = Text("ERROR: " + short(rule.error, 30), style="red") if rule.error else (
                Text("suggested — approve", style="yellow") if rule.proposed else Text("active" if rule.enabled else "off", style="dim"))
            r.add_row(on, rule.text, st, key=rule.id)
        if keep:
            try:
                r.move_cursor(row=r.get_row_index(keep), animate=False)
            except Exception:
                pass
        i = self.query_one("#ru-ins", DataTable)
        keep = self.cur_ins
        i.clear()
        for ins in s.instructions.items:
            i.add_row(Text("✔", style="green") if ins.enabled else Text("·", style="dim"), ins.scope_label(), ins.task, ins.text, key=ins.id)
        if keep:
            try:
                i.move_cursor(row=i.get_row_index(keep), animate=False)
            except Exception:
                pass

    def on_data_table_row_highlighted(self, ev: DataTable.RowHighlighted) -> None:
        key = ev.row_key.value if ev.row_key is not None else None
        if ev.data_table.id == "ru-rules":
            self.cur_rule = key
        else:
            self.cur_ins = key

    def on_input_submitted(self, ev: Input.Submitted) -> None:
        if ev.input.id == "ru-new" and ev.value.strip():
            self.app.run_command(ev.value.strip())
            ev.input.value = ""

    def _which(self) -> str:
        f = self.app.focused
        return "ins" if f is not None and f.id == "ru-ins" else "rules"

    # ------------------------------------------------------------------ actions
    def action_toggle(self) -> None:
        s = self.session
        if self._which() == "rules" and self.cur_rule:
            s.toggle_rule(self.cur_rule)
        elif self.cur_ins:
            s.toggle_instruction(self.cur_ins)

    def action_approve(self) -> None:
        r = self.session.rules.get(self.cur_rule) if self.cur_rule else None
        if r is not None and r.proposed:
            self.session.toggle_rule(r.id)
            self.toast("rule approved and applied — see Stage")

    def action_delete(self) -> None:
        s = self.session
        if self._which() == "rules" and self.cur_rule:
            s.remove_rule(self.cur_rule)
            self.cur_rule = None
        elif self.cur_ins:
            s.remove_instruction(self.cur_ins)
            self.cur_ins = None

    def action_edit(self) -> None:
        s = self.session
        if self._which() == "rules" and self.cur_rule:
            r = s.rules.get(self.cur_rule)

            def done(txt):
                if txt and txt.strip():
                    try:
                        s.update_rule(r.id, txt)
                    except Exception as e:  # noqa: BLE001
                        self.toast(str(e), "error")
            self.app.push_screen(PromptModal("Edit rule", "conditions -> actions", r.text), done)
        elif self.cur_ins:
            ins = s.instructions.get(self.cur_ins)

            def done_i(txt):
                if txt and txt.strip():
                    ins.text = txt.strip()
                    s.instructions.save()
                    s.emit("rules")
            self.app.push_screen(PromptModal("Edit instruction", "", ins.text), done_i)

    def action_up(self) -> None:
        if self._which() == "rules" and self.cur_rule:
            self.session.move_rule(self.cur_rule, -1)

    def action_down(self) -> None:
        if self._which() == "rules" and self.cur_rule:
            self.session.move_rule(self.cur_rule, 1)

    def action_count(self) -> None:
        if not self.cur_rule:
            return
        hits = self.session.rule_matches(self.cur_rule)
        body = f"{len(hits)} note(s) match.\n\n" + "\n".join(hits[:60]) + ("\n…" if len(hits) > 60 else "")
        self.app.push_screen(InfoModal("Notes matching this rule", body))

    def action_rerun(self) -> None:
        if self._which() == "ins" and self.cur_ins:
            ins = self.session.instructions.get(self.cur_ins)
            job = self.session.rerun_affected(ins)
            self.toast(f"re-running AI for {job.total} note(s) — job #{job.id}" if job else "no notes in scope")
