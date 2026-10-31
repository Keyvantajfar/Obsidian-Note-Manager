import os
from pathlib import Path

from conftest import snapshot
from vaultcore import apply as ap
from vaultcore.session import Session


def test_apply_then_undo_restores_vault_byte_for_byte(session, vault):
    before = snapshot(vault)
    mtimes = {p: os.stat(vault / p).st_mtime_ns for p in before if not p.endswith("/")}
    session.taxonomy.add("1_Z")
    session.taxonomy.add("2_R/AI")
    for rel in list(session.index.notes):
        session.stage_header(rel, {"status": "Idea", "tags": ["T1", "T2"], "parent": "P", "date": "2020-01-01T00:00"})
    session.stage_move("Zettelkasten/Attention.md", "2_R/AI")
    session.stage_move("Research/Notes.md", "2_R/AI", name="Research notes renamed")
    res = session.apply()
    assert not res.failed and res.batch_id
    assert (vault / "2_R/AI/Attention.md").exists() and (vault / "2_R/AI/Research notes renamed.md").exists()
    assert not (vault / "Zettelkasten/Attention.md").exists()
    assert snapshot(vault) != before
    r = session.undo()
    assert not r.conflicts, r.conflicts
    after = snapshot(vault)
    # folders created by the batch are removed again; everything else is identical
    assert after == before
    for p, m in mtimes.items():
        assert os.stat(vault / p).st_mtime_ns == m, p


def test_headers_written_keep_body_and_mtime(session, vault):
    rel = "Zettelkasten/Windows note.md"
    old = (vault / rel).read_bytes()
    old_m = os.stat(vault / rel).st_mtime_ns
    session.stage_header(rel, {"status": "Idea"})
    session.apply()
    new = (vault / rel).read_bytes()
    assert new.endswith(old) and new.startswith(b"---\r\nstatus: #Idea\r\n---\r\n")
    assert os.stat(vault / rel).st_mtime_ns == old_m


def test_collision_suffix_never_overwrites(session, vault):
    a = (vault / "University/Notes.md").read_bytes()
    b = (vault / "Research/Notes.md").read_bytes()
    session.stage_move("University/Notes.md", "Research")
    plan = session.plan_apply()
    item = plan.items[0]
    assert item.dst == "Research/Notes (1).md" and item.warned
    session.apply(plan)
    assert (vault / "Research/Notes.md").read_bytes() == b
    assert (vault / "Research/Notes (1).md").read_bytes() == a


def test_collision_skip_policy_blocks(session, vault):
    session.settings.collision_policy = "skip"
    session.stage_move("University/Notes.md", "Research")
    plan = session.plan_apply()
    assert plan.items[0].blocked
    res = session.apply(plan)
    assert (vault / "University/Notes.md").exists() and not res.applied


def test_two_moves_to_same_target_get_distinct_names(session, vault):
    session.stage_move("University/Notes.md", "Inbox", name="X")
    session.stage_move("Research/Notes.md", "Inbox", name="X")
    plan = session.plan_apply()
    dsts = sorted(i.dst for i in plan.items)
    assert dsts == ["Inbox/X (1).md", "Inbox/X.md"]


def test_unsafe_paths_rejected(session):
    import pytest
    for bad in ["../outside", ".hidden", "C:/win", "a/../../b"]:
        with pytest.raises(ValueError):
            session.stage_move("Inbox/Link note.md", bad)
    c = session.stage_move("Inbox/Link note.md", "/abs/path")      # leading slash is just relative to the vault
    assert c.data["folder"] == "abs/path"
    session.stage.clear()
    c = session.stage_move("Inbox/Link note.md", "Fine", name="../../evil")
    plan = session.plan_apply()
    assert plan.items[0].dst == "Fine/evil.md"                       # path parts in the name are dropped


def test_locked_and_missing_notes_are_blocked(session, vault):
    session.toggle_lock("Inbox/Link note.md")
    session.stage_header("Inbox/Link note.md", {"status": "Idea"})
    session.stage_header("Inbox/Unsorted thought.md", {"status": "Idea"})
    os.remove(vault / "Inbox/Unsorted thought.md")
    plan = session.plan_apply()
    assert all(i.blocked for i in plan.items)


def test_note_edited_after_proposal_is_merged_against_current_text(session, vault):
    rel = "Zettelkasten/idea.md"
    session.stage_header(rel, {"status": "Idea", "parent": "P"}, source="ai")
    session.accept([c.id for c in session.stage.all()])
    p = vault / rel
    p.write_text('---\nparent: "[[Mine]]"\n---\nQuick idea, edited meanwhile.\n')
    plan = session.plan_apply()
    session.apply(plan)
    text = p.read_text()
    assert '"[[Mine]]"' in text and "[[P]]" not in text and "status: #Idea" in text and "edited meanwhile" in text


def test_undo_reports_conflict_when_note_edited_since(session, vault):
    rel = "Zettelkasten/idea.md"
    session.stage_header(rel, {"status": "Idea"})
    session.apply()
    p = vault / rel
    p.write_text(p.read_text() + "\nmy later edit\n")
    r = session.undo()
    assert any("edited since" in c for c in r.conflicts)
    assert "my later edit" in p.read_text()


def test_prune_empty_dirs_and_undo_recreates(session, vault):
    for rel in ["Inbox/Link note.md", "Inbox/Unsorted thought.md"]:
        session.stage_move(rel, "Zettelkasten")
    session.apply()
    assert not (vault / "Inbox").exists()
    session.undo()
    assert (vault / "Inbox/Link note.md").exists()


def test_journal_batches_listed_and_undone_flag(session):
    session.stage_header("Zettelkasten/idea.md", {"status": "Idea"})
    session.apply()
    b = session.batches()
    assert len(b) == 1 and not b[0].undone
    session.undo()
    assert session.batches()[0].undone
    assert session.undo().batch_id == ""          # nothing left to undo


def test_save_note_text_is_undoable(session, vault):
    rel = "Zettelkasten/idea.md"
    orig = (vault / rel).read_bytes()
    session.save_note(rel, "edited body\n")
    assert (vault / rel).read_text() == "edited body\n"
    session.undo()
    assert (vault / rel).read_bytes() == orig


def test_link_warnings(session):
    w = session.index.move_warnings("Zettelkasten/Attention.md", "2_R/Attention.md")
    assert any("Inbox/Link note.md" in x for x in w)
    w2 = session.index.move_warnings("Inbox/Link note.md", "X/Link note.md")
    assert any("relative markdown" in x for x in w2)
    w3 = session.index.move_warnings("University/Notes.md", "Research/Notes.md")
    assert any("ambiguous" in x for x in w3)


def test_noop_changes_are_cleared(session):
    session.stage_header("Zettelkasten/Complete header.md", {"status": "Permanent"})
    session.stage_move("Zettelkasten/idea.md", "Zettelkasten")
    res = session.apply()
    assert len(session.stage) == 0 and not res.batch_id
