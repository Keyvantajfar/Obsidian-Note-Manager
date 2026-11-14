import os
from datetime import datetime

from vaultcore import dates
from vaultcore.datefmt import date_in_name, parse_date_loose
from vaultcore.settings import Settings


def _set_mtime(p, dt):
    ts = dt.timestamp()
    os.utime(p, (ts, ts))


def test_parse_loose():
    assert parse_date_loose("2024-09-16T17:18") == datetime(2024, 9, 16, 17, 18)
    assert parse_date_loose('"[[2024/9/6 07:05:09]]"') == datetime(2024, 9, 6, 7, 5, 9)
    assert parse_date_loose("16/09/2024") is None            # ambiguous forms are not guessed
    assert parse_date_loose("2024-13-40") is None
    assert date_in_name("Journal 2024-01-02.md") == datetime(2024, 1, 2)
    assert date_in_name("20240102 meeting.md") == datetime(2024, 1, 2)
    assert date_in_name("Chapter 1999x.md") is None


def test_default_date_source_is_earliest():
    assert Settings().date_source == "earliest"


def test_statuses_and_fix(tmp_path, monkeypatch):
    from vaultcore.session import Session
    monkeypatch.setenv("VAULTMGR_HOME", str(tmp_path / "cfg"))
    v = tmp_path / "v"
    v.mkdir()
    old = datetime(2023, 3, 1, 10, 0)
    notes = {
        "missing.md": "---\nstatus: #Idea\n---\nbody\n",
        "bad.md": "---\ndate: sometime last week\n---\nbody\n",
        "fmt.md": "---\ndate: 2023/03/01 10:00\n---\nbody\n",
        "later.md": "---\ndate: 2026-10-30T22:00\n---\nbody\n",      # copy-time stamp
        "ok.md": "---\ndate: 2023-03-01T10:00\n---\nbody\n",
        "edited.md": "---\ndate: 2020-01-01T00:00\n---\nbody\n",     # created long before the last edit: normal
        "Journal 2021-05-04.md": "---\ndate: 2026-10-30T22:00\n---\nbody\n",
    }
    for n, txt in notes.items():
        (v / n).write_text(txt, encoding="utf-8")
        _set_mtime(v / n, old)
    s = Session(v)
    s.scan()
    rows = {r.rel: r for r in s.date_rows()}
    assert {k: r.status for k, r in rows.items()} == {
        "missing.md": "missing", "bad.md": "unreadable", "fmt.md": "format", "later.md": "later",
        "ok.md": "ok", "edited.md": "ok", "Journal 2021-05-04.md": "later"}
    assert rows["fmt.md"].proposed == "2023-03-01T10:00"            # same date, new spelling
    assert rows["later.md"].proposed == "2023-03-01T10:00"
    assert s.date_rows(flag_earlier=True)[[r.rel for r in s.date_rows()].index("edited.md")].status == "earlier"
    named = {r.rel: r for r in s.date_rows(source="name_or_earliest")}
    assert named["Journal 2021-05-04.md"].proposed == "2021-05-04T00:00"

    before = {n: (v / n).read_bytes() for n in notes}
    assert s.fix_dates([r for r in rows.values()]) == 5
    assert {n: (v / n).read_bytes() for n in notes} == before        # staging writes nothing
    s.accept_all()
    res = s.apply(s.plan_apply())
    assert not res.failed
    s.scan()
    assert all(r.status == "ok" for r in s.date_rows())
    assert (v / "later.md").read_text(encoding="utf-8") == "---\ndate: 2023-03-01T10:00\n---\nbody\n"
    s.undo()
    s.scan()
    assert {n: (v / n).read_bytes() for n in notes} == before


def test_offline_session_never_calls_the_model(tmp_path, monkeypatch):
    from vaultcore.demo import make_demo_vault
    from vaultcore.mock import MockLLM
    from vaultcore.session import Session
    monkeypatch.setenv("VAULTMGR_HOME", str(tmp_path / "cfg"))
    llm = MockLLM()
    s = Session(make_demo_vault(tmp_path / "v"), llm=llm)
    s.scan()
    s.offline = True
    assert s.run_ai(None) is None and llm.calls == 0
    s.offline = False
    s.route_enabled = False
    job = s.run_ai(None, header=False, route=True)
    assert job is None
