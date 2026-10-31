"""The Textual application: status bar, eight tabs, a global command bar."""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import tempfile
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Input, Static, TabbedContent, TabPane

from vaultcore.mock import MockLLM
from vaultcore.session import Session
from vaultcore.settings import AUTO_LEVELS

from .modals import InfoModal
from .tabs.explorer import ExplorerTab
from .tabs.jobs import JobsTab
from .tabs.organizer import OrganizerTab
from .tabs.rules import RulesTab
from .tabs.settings import SettingsTab
from .tabs.stage import StageTab
from .tabs.tagger import TaggerTab
from .tabs.tags import TagsTab

TAB_CLASSES = [
    ("explorer", "1 Explorer", ExplorerTab),
    ("tagger", "2 Tagger", TaggerTab),
    ("organizer", "3 Organizer", OrganizerTab),
    ("stage", "4 Stage", StageTab),
    ("jobs", "5 Jobs", JobsTab),
    ("tags", "6 Tags", TagsTab),
    ("rules", "7 Rules", RulesTab),
    ("settings", "8 Settings", SettingsTab),
]

MODE_STYLE = {"manual": "white on grey37", "assist": "black on yellow", "auto": "white on dark_green"}

HELP = """[b]Global keys[/]
  1-8       switch tabs               :  or  /     command bar
  p         pause / resume all AI jobs (in-flight requests finish)
  m         cycle mode: manual → assist → auto
  ctrl+p    command palette (themes, ...)          q   save & quit

[b]Modes[/]
  manual    nothing runs by itself; you start jobs, results land in the Stage as "proposed"
  assist    new/changed notes are picked up in the background and proposed; you accept
  auto      like assist, plus proposals with confidence ≥ threshold are auto-accepted
            (optionally auto-applied, capped — Settings ▸ Auto & jobs). Everything is undoable.

[b]Command bar[/]
  plain text                      → an instruction for the AI, e.g.  keep calculus notes under 3_University/Math
  @Folder/Path: text              → instruction scoped to a folder       #tag: text   → scoped to a tag
  tag:ML -> move:2_Research/AI    → a rule (deterministic, applied instantly; see Rules tab)
  /mode manual|assist|auto   /workers N   /batch N   /threshold 0.8   /run [tag|route|all]
  /apply   /undo   /pause   /resume   /stop   /scan   /freeze   /unfreeze   /goto <tab>   /help

[b]Workflow[/]  Explorer (look / edit by hand) → Tagger (AI headers) → Organizer (plan folders, route)
          → Stage (review, edit, dry-run, apply) → Undo any time.   Nothing is written until you Apply."""


class StatusBar(Static):
    def refresh_view(self, s: Session, paused: bool) -> None:
        st = s.status()
        jobs = s.jobs.active()
        total = sum(j.total for j in jobs)
        done = sum(j.finished_count for j in jobs)
        rate = sum(j.rate for j in jobs)
        stage = st["stage"]
        mode = s.settings.auto_level
        t = Text()
        t.append(" Vault Manager ", style="bold white on dark_blue")
        t.append(f" {Path(s.vault).name} ", style="bold")
        t.append(f"│ {s.model_label} ")
        t.append(f" {mode.upper()} ", style=MODE_STYLE.get(mode, ""))
        if paused:
            t.append(" PAUSED ", style="bold white on red")
        t.append(f" │ jobs {len(jobs)}" + (f" ({done}/{total}, {rate:.1f}/s)" if jobs else ""))
        t.append(f" │ stage ● {stage['proposed']}  ✔ {stage['accepted']}")
        t.append(f" │ notes {st['notes']} ({st['incomplete']} need header)")
        if st["tokens"]:
            t.append(f" │ {st['tokens']:,} tok")
        if s.taxonomy.frozen:
            t.append(" │ plan frozen", style="dim")
        self.update(t)


class VaultApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "Vault Manager"
    BINDINGS = [
        *[Binding(str(i + 1), f"tab('{tid}')", label, show=False) for i, (tid, label, _) in enumerate(TAB_CLASSES)],
        Binding(":", "command", "Command", key_display=":"),
        Binding("slash", "command", "Command", show=False),
        Binding("p", "toggle_pause", "Pause/resume jobs"),
        Binding("m", "cycle_mode", "Mode"),
        Binding("question_mark", "help", "Help", key_display="?"),
        Binding("q", "quit_app", "Quit"),
    ]

    def __init__(self, session: Session, **kw):
        super().__init__(**kw)
        self.session = session
        self._stale: set[str] = set()
        self._events: set[str] = set()
        self._tabs: dict[str, object] = {}
        self._paused = False

    # ------------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        yield StatusBar(id="statusbar")
        with TabbedContent(initial="explorer", id="tabs"):
            for tid, label, cls in TAB_CLASSES:
                with TabPane(label, id=tid):
                    w = cls(self.session, id=f"tab-{tid}")
                    self._tabs[tid] = w
                    yield w
        yield Input(placeholder="command: instruction for the AI · rule (cond -> action) · /help", id="cmdbar")
        yield Static("", id="msgbar")
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#cmdbar", Input).display = False
        self.call_after_refresh(self._tabs["explorer"].focus_main)
        self.session.on(self._on_event)
        self.set_interval(0.3, self._flush)
        try:
            self.theme = self.session.settings.theme
        except Exception:
            pass
        self.query_one("#msgbar", Static).update("[dim]scanning vault…[/]")
        diff = await asyncio.to_thread(self.session.index.scan)
        self.session.after_scan(diff)
        self.session.start_background()
        self._mark_all_stale()
        self.query_one("#msgbar", Static).update("[dim]ready — press ? for help[/]")
        self.query_one("#statusbar", StatusBar).refresh_view(self.session, self._paused)

    # ------------------------------------------------------------------ events from the core
    def _on_event(self, event: str, payload: dict) -> None:
        if event == "message":
            self.query_one("#msgbar", Static).update(payload.get("text", ""))
            return
        if event == "applied":
            r = payload["result"]
            self.notify(f"applied {len(r.applied)} change(s)" + (f", {len(r.failed)} failed" if r.failed else "")
                        + (f" — batch {r.batch_id}" if r.batch_id else ""), severity="warning" if r.failed else "information")
        elif event == "undone":
            r = payload["result"]
            self.notify(f"undid batch {r.batch_id or '—'}: {r.restored} header(s), {r.moved_back} move(s)"
                        + (f", {len(r.conflicts)} conflict(s)" if r.conflicts else ""),
                        severity="warning" if r.conflicts else "information")
        elif event == "confirm_needed":
            self.notify(f"{payload['count']} accepted changes exceed the auto-apply cap — review in Stage (4)", severity="warning")
        elif event == "job" and payload.get("event") == "finished":
            j = payload["job"]
            if j is not None:
                self.notify(f"job #{j.id} {j.state}: {j.count('done')} done, {j.count('error')} failed", severity="warning" if j.errors else "information")
        self._events.add(event)

    def _mark_all_stale(self) -> None:
        self._stale |= set(self._tabs)

    def _flush(self) -> None:
        if self._events:
            ev, self._events = self._events, set()
            for tid, w in self._tabs.items():
                if w.WATCH & ev:
                    self._stale.add(tid)
        try:
            active = self.query_one("#tabs", TabbedContent).active
        except Exception:
            return
        w = self._tabs.get(active)
        if w is not None and active in self._stale:
            if w.maybe_refresh():
                self._stale.discard(active)
        try:
            self.query_one("#statusbar", StatusBar).refresh_view(self.session, self._paused)
        except Exception:
            pass

    def on_tabbed_content_tab_activated(self, ev: TabbedContent.TabActivated) -> None:
        tid = ev.pane.id
        w = self._tabs.get(tid)
        if w is not None and tid in self._stale and w.is_mounted:
            w.maybe_refresh(force=True)
            self._stale.discard(tid)
        if w is not None:
            self.call_after_refresh(w.focus_main)

    # ------------------------------------------------------------------ global actions
    def action_tab(self, tid: str) -> None:
        self.query_one("#tabs", TabbedContent).active = tid
        w = self._tabs.get(tid)
        if w is not None:
            self.call_after_refresh(w.focus_main)

    def action_toggle_pause(self) -> None:
        jobs = self.session.jobs.active()
        if not jobs and not self._paused:
            self.notify("no running jobs")
            return
        if any(j.state == "paused" for j in jobs):
            self.session.jobs.resume_all()
            self._paused = False
            self.session.paused_watch = False
            self.notify("resumed")
        else:
            self.session.jobs.pause_all()
            self._paused = True
            self.session.paused_watch = True
            self.notify("paused — in-flight requests finish, nothing new starts (p resumes)")

    def action_cycle_mode(self) -> None:
        s = self.session
        i = (AUTO_LEVELS.index(s.settings.auto_level) + 1) % len(AUTO_LEVELS)
        s.settings.auto_level = AUTO_LEVELS[i]
        s.save_settings()
        self.notify(f"mode: {AUTO_LEVELS[i]}")

    def action_help(self) -> None:
        self.push_screen(InfoModal("Help", HELP, wide=True))

    def action_command(self) -> None:
        bar = self.query_one("#cmdbar", Input)
        bar.display = True
        bar.value = ""
        bar.focus()

    def action_quit_app(self) -> None:
        self.run_worker(self._quit(), exclusive=True)

    async def _quit(self) -> None:
        await self.session.aclose()
        self.exit()

    # ------------------------------------------------------------------ command bar
    def on_key(self, event) -> None:
        bar = self.query_one("#cmdbar", Input)
        if event.key == "escape" and bar.display:
            bar.display = False
            self._refocus()

    def on_input_submitted(self, ev: Input.Submitted) -> None:
        if ev.input.id != "cmdbar":
            return
        text = ev.value.strip()
        ev.input.display = False
        ev.input.value = ""
        self._refocus()
        if text:
            self.run_command(text)

    def _refocus(self) -> None:
        try:
            w = self._tabs.get(self.query_one("#tabs", TabbedContent).active)
            if w is not None:
                self.call_after_refresh(w.focus_main)
        except Exception:
            self.set_focus(None)

    def run_command(self, text: str) -> None:
        s = self.session
        if text.startswith("/"):
            return self._slash(text[1:].strip())
        if "->" in text:
            try:
                r = s.add_rule(text)
                n = len(s.rule_matches(r.id))
                self.notify(f"rule added — matches {n} note(s). Review the results in Stage (4).")
            except Exception as e:  # noqa: BLE001
                self.notify(str(e), severity="error")
            return
        scope, value, body = "global", "", text
        m = re.match(r"^@(\S+?):\s*(.+)$", text)
        m2 = re.match(r"^#(\S+?):\s*(.+)$", text)
        if m:
            scope, value, body = "folder", m.group(1).strip("/"), m.group(2)
        elif m2:
            scope, value, body = "tag", m2.group(1), m2.group(2)
        ins, job = s.add_instruction(body, scope, value)
        self.notify(f"instruction saved ({ins.scope_label()})" + (f" — re-running AI on affected notes (job #{job.id})" if job else
                    " — press 'R' on the Tagger / use /run to apply it"))
        self.run_worker(self._translate(body), exclusive=False)

    async def _translate(self, text: str) -> None:
        try:
            rules, expl = await self.session.translate_instruction(text)
        except Exception as e:  # noqa: BLE001
            self.notify(f"could not turn it into rules: {e}", severity="warning")
            return
        for r in rules:
            try:
                self.session.add_rule(r, name="from instruction", enabled=False, proposed=True)
            except Exception:
                pass
        if rules:
            self.notify(f"{len(rules)} rule suggestion(s) added (disabled) — approve them in Rules (7)")

    def _slash(self, cmd: str) -> None:
        s = self.session
        parts = cmd.split()
        if not parts:
            return
        name, args = parts[0].lower(), parts[1:]
        try:
            if name == "mode" and args and args[0] in AUTO_LEVELS:
                s.settings.auto_level = args[0]
                s.save_settings()
                self.notify(f"mode: {args[0]}")
            elif name == "workers" and args:
                s.settings.workers = int(args[0])
                s.save_settings()
                for j in s.jobs.active():
                    j.set_workers(s.settings.workers)
                self.notify(f"workers: {s.settings.workers}")
            elif name == "batch" and args:
                s.settings.batch_size = int(args[0])
                s.save_settings()
                self.notify(f"notes per request: {s.settings.batch_size} (applies to new jobs)")
            elif name == "threshold" and args:
                s.settings.auto_accept_threshold = float(args[0])
                s.save_settings()
                self.notify(f"auto-accept threshold: {s.settings.auto_accept_threshold}")
            elif name == "run":
                what = args[0] if args else "all"
                job = s.run_ai(header=what in ("tag", "all"), route=what in ("route", "all"), only_unstaged=True)
                self.notify(f"job #{job.id} started" if job else "nothing to do")
            elif name == "apply":
                self.action_tab("stage")
                self._tabs["stage"].action_apply()
            elif name == "undo":
                self.action_tab("stage")
                self._tabs["stage"].action_undo()
            elif name == "pause":
                s.jobs.pause_all()
            elif name == "resume":
                s.jobs.resume_all()
            elif name == "stop":
                s.jobs.cancel_all()
            elif name == "import" and args:
                prof = s.profiles.import_legacy(" ".join(args).strip('"'))
                s.use_profile(prof)
                self.notify(f"imported + activated LLM profile '{prof.name}' (check Settings → LLM connection → Test)")
            elif name == "scan":
                self.run_worker(self._rescan())
            elif name in ("freeze", "unfreeze"):
                s.set_frozen(name == "freeze")
                self.notify("plan frozen: AI may only suggest new folders" if name == "freeze" else "plan unfrozen")
            elif name == "goto" and args:
                ids = {t[0]: t[0] for t in TAB_CLASSES}
                match = next((t for t in ids if t.startswith(args[0].lower())), None)
                if match:
                    self.action_tab(match)
            elif name == "help":
                self.action_help()
            else:
                self.notify(f"unknown command: /{cmd}  (try /help)", severity="warning")
        except (ValueError, IndexError) as e:
            self.notify(f"bad argument: {e}", severity="error")

    async def _rescan(self) -> None:
        diff = await asyncio.to_thread(self.session.index.scan)
        self.session.after_scan(diff)
        self.notify("rescanned")


# --------------------------------------------------------------------------- entry point

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="vaultmgr", description="AI-assisted Obsidian vault tagger & organizer (TUI)")
    ap.add_argument("vault", nargs="?", help="path to the Obsidian vault (default: last used, or the current directory)")
    ap.add_argument("--mock", action="store_true", help="use a built-in fake model (try the UI without an LLM server)")
    ap.add_argument("--demo", action="store_true", help="create a messy sample vault in a temp dir and open it (implies --mock)")
    ap.add_argument("--import-config", metavar="FILE", help="import LLM settings from an old config.json (ai_base_url / ai_model / ai_api_key)")
    args = ap.parse_args(argv)

    from vaultcore.demo import make_demo_vault
    from vaultcore.settings import Profiles

    if args.demo:
        vault = make_demo_vault(Path(tempfile.mkdtemp(prefix="vaultmgr-demo-")) / "Demo Vault")
        args.mock = True
        print(f"demo vault: {vault}", file=sys.stderr)
    elif args.vault:
        vault = Path(args.vault).expanduser()
    else:
        recent = Profiles.load().recent_vaults
        vault = Path(recent[0]) if recent and Path(recent[0]).is_dir() else Path.cwd()
    if not vault.is_dir():
        print(f"vault not found: {vault}", file=sys.stderr)
        return 2
    if args.import_config:
        try:
            prof = Profiles.load().import_legacy(args.import_config)
        except (OSError, ValueError) as e:
            print(f"cannot import {args.import_config}: {e}", file=sys.stderr)
            return 2
        print(f"imported LLM profile '{prof.name}' (model {prof.model or '-'}) and made it active", file=sys.stderr)
    session = Session(vault, llm=MockLLM(delay=0.25, jitter=0.5) if args.mock else None)
    VaultApp(session).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
