"""Home: choose what you want to do. Each tile opens the same window with only the tabs that workflow needs."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Grid, Vertical
from textual.message import Message
from textual.widgets import Static


#BANNER = r"""
#██╗   ██╗ █████╗ ██╗   ██╗██╗  ████████╗
#██║   ██║██╔══██╗██║   ██║██║  ╚══██╔══╝
#██║   ██║███████║██║   ██║██║     ██║
#╚██╗ ██╔╝██╔══██║██║   ██║██║     ██║
# ╚████╔╝ ██║  ██║╚██████╔╝███████╗██║
#  ╚═══╝  ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝
#"""

BANNER = r"""
 ██████╗ ██████╗ ███████╗██╗██████╗ ██╗ █████╗ ███╗   ██╗
██╔═══██╗██╔══██╗██╔════╝██║██╔══██╗██║██╔══██╗████╗  ██║
██║   ██║██████╔╝███████╗██║██║  ██║██║███████║██╔██╗ ██║
██║   ██║██╔══██╗╚════██║██║██║  ██║██║██╔══██║██║╚██╗██║
╚██████╔╝██████╔╝███████║██║██████╔╝██║██║  ██║██║ ╚████║
 ╚═════╝ ╚═════╝ ╚══════╝╚═╝╚═════╝ ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝

__   ___  _   _ _  _____   __  __   _   _  _   _   ___ ___ ___
\ \ / /_\| | | | ||_   _| |  \/  | /_\ | \| | /_\ / __| __| _ \
 \ V / _ \ |_| | |__| |   | |\/| |/ _ \| .` |/ _ \ (_ | _||   /
  \_/_/ \_\___/|____|_|   |_|  |_/_/ \_\_|\_/_/ \_\___|___|_|_\
"""

_w = max(len(l) for l in BANNER.splitlines())
BANNER = "\n".join(l.ljust(_w) for l in BANNER.strip("\n").splitlines())     # equal widths so centring never shears the art

# id -> (title, ascii art, description, tabs in order, needs an LLM?)
WORKFLOWS: dict[str, tuple[str, str, str, list[str], bool]] = {
    "all": ("ALL", "tag + route + dates\n  →  stage  →  apply",
            "Everything, in one window:\nheaders, folders, dates,\nreview and undo", 
            ["explorer", "tagger", "organizer", "dates", "stage", "jobs", "tags", "rules", "settings"], True),
    "header": ("HEADER", "---\ntags: [[A]], [[B]]\n---",
               "Tag notes and fix headers:\nparent, status, tags, date.\nNo folder moves", 
               ["explorer", "tagger", "dates", "stage", "jobs", "tags", "rules", "settings"], True),
    "organizer": ("ORGANIZER", "Inbox/  ->  Research/\n          ->  University/",
                  "Plan your folders, route notes\ninto them, review the moves.\nHeaders stay untouched",
                  ["explorer", "organizer", "stage", "jobs", "rules", "settings"], True),
    "dates": ("DATE FIXER", "2024-09-16T17:18",
              "Fix missing, broken or copy-time\ndates from file timestamps.\nOffline: no model, no API calls",
              ["explorer", "dates", "stage", "settings"], False),
}
ORDER = list(WORKFLOWS)
LANDING = {"all": "explorer", "header": "tagger", "organizer": "organizer", "dates": "dates"}   # tab shown on entry


class Tile(Static, can_focus=True):
    BINDINGS = [Binding("enter", "choose", "Open")]

    class Chosen(Message):
        def __init__(self, wid: str):
            super().__init__()
            self.wid = wid

    def __init__(self, wid: str, key: str):
        title, art, desc, _tabs, _llm = WORKFLOWS[wid]
        super().__init__(f"\n[b]{art}[/b]\n\n[b]{title}[/b]  [dim]\\[{key}][/dim]\n[dim]{desc}[/dim]", id=f"tile-{wid}", classes="tile")
        self.wid = wid

    def on_click(self) -> None:
        self.action_choose()

    def action_choose(self) -> None:
        self.post_message(self.Chosen(self.wid))


class HomeView(Vertical):
    COLS = 2     # must match `grid-size` of #tiles in app.tcss
    BINDINGS = [Binding("right", "move(0,1)", show=False), Binding("left", "move(0,-1)", show=False),
                Binding("down", "move(1,0)", show=False), Binding("up", "move(-1,0)", show=False)]

    def compose(self) -> ComposeResult:
        yield Static(BANNER, id="banner")
        yield Static("tag · organize · fix dates in your notes", id="tagline")
        with Center():
            with Grid(id="tiles"):
                for i, wid in enumerate(ORDER, 1):
                    yield Tile(wid, str(i))
        yield Static("", id="home-info")
        yield Static("[b]\\[1-4][/b] open   [b]\\[Q][/b] quit   ·   [b]\\[Esc][/b] comes back here from any workspace", id="home-foot")

    def on_mount(self) -> None:
        self.query_one("#banner").styles.height = "auto"       # the logo is taller than the old default

    def focus_tile(self, wid: str = "all") -> None:
        try:
            self.query_one(f"#tile-{wid}").focus()
        except Exception:
            pass

    def set_info(self, text: str) -> None:
        self.query_one("#home-info", Static).update(text)

    def action_move(self, drow: int, dcol: int) -> None:
        """Arrow keys move in their own direction across the tile grid; at an edge they stay put (no wrap, no bounce)."""
        focused = self.screen.focused
        if not isinstance(focused, Tile):
            return
        i = ORDER.index(focused.wid)
        row, col = divmod(i, self.COLS)
        rows = -(-len(ORDER) // self.COLS)
        row, col = row + drow, col + dcol
        if not (0 <= row < rows and 0 <= col < self.COLS):
            return
        j = row * self.COLS + col
        if j < len(ORDER):
            self.focus_tile(ORDER[j])