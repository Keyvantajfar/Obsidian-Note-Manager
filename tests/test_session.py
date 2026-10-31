import asyncio

import pytest

from vaultcore.mock import MockLLM
from vaultcore.session import Session


def setup_plan(s: Session):
    s.taxonomy.add("1_Zettelkasten", "atomic evergreen notes ideas")
    s.taxonomy.add("2_Research/AI", "machine learning neural network transformers attention")
    s.taxonomy.add("3_University", "course lecture exam statistics")
    s.taxonomy.add("4_Personal", "journal recipe personal family")


async def test_parallel_workers_really_run_concurrently(vault):
    llm = MockLLM(delay=0.05)
    s = Session(vault, llm=llm); s.scan(); s.settings.workers = 6
    job = s.run_ai(route=False)
    await job.wait()
    assert llm.max_in_flight >= 4
    assert job.count("done") == len(job.items) and llm.max_in_flight <= 6
    await s.aclose()


async def test_single_worker_is_sequential(vault):
    llm = MockLLM(delay=0.01)
    s = Session(vault, llm=llm); s.scan(); s.settings.workers = 1
    await s.run_ai(route=False).wait()
    assert llm.max_in_flight == 1
    await s.aclose()


async def test_micro_batching_reduces_calls_and_still_covers_all(vault):
    llm = MockLLM()
    s = Session(vault, llm=llm); s.scan(); s.settings.batch_size = 5
    job = s.run_ai(route=False)
    await job.wait()
    n_llm = sum(1 for i in job.items.values() if i.state == "done")
    assert llm.calls <= -(-n_llm // 5) + 1                      # ceil(n/5) (+ possible singles)
    for rel in job.items:
        assert s.stage.get(rel, "header") is not None
    await s.aclose()


async def test_second_run_is_served_from_cache(session, llm):
    setup_plan(session)
    await session.run_ai().wait()
    calls = llm.calls
    assert calls > 0
    session.stage.clear()
    await session.run_ai().wait()
    assert llm.calls == calls                                    # zero new LLM calls
    assert len(session.stage) > 0


async def test_adding_a_folder_requeries_route_only_not_headers(session, llm):
    setup_plan(session)
    await session.run_ai().wait()
    llm.prompts.clear()
    job = session.add_folder("2_Research/AI/Diffusion", "diffusion models image generation", reroute=True)
    await job.wait()
    assert llm.prompts, "should re-route"
    assert all("Fields to fill" not in sys for sys, _ in llm.prompts)       # headers were cached
    assert all("FOLDERS:" in u for _, u in llm.prompts)


async def test_instruction_only_reruns_affected_notes(session, llm):
    setup_plan(session)
    await session.run_ai().wait()
    llm.prompts.clear()
    ins, job = session.add_instruction("put everything about calculus into 3_University", scope="folder",
                                       value="Zettelkasten", rerun=True)
    await job.wait()
    n_scope = sum(1 for n in session.index.notes.values() if n.folder == "Zettelkasten")
    assert 0 < len(llm.prompts) <= n_scope
    assert all("USER INSTRUCTIONS" in u for _, u in llm.prompts)
    joined = "".join(u for _, u in llm.prompts)
    assert "title: Statistics exam" not in joined and "title: Attention" in joined      # outside scope untouched


async def test_pause_resume_cancel_and_live_worker_change(vault):
    llm = MockLLM(delay=0.05)
    s = Session(vault, llm=llm); s.scan(); s.settings.workers = 1
    job = s.run_ai(route=False)
    await asyncio.sleep(0.12)
    done_before = job.finished_count
    job.pause()
    await asyncio.sleep(0.2)
    frozen = job.finished_count
    assert frozen - done_before <= 1 and job.state == "paused"
    await asyncio.sleep(0.15)
    assert job.finished_count == frozen                           # nothing progresses while paused
    job.set_workers(8)
    job.resume()
    await asyncio.sleep(0.12)
    assert llm.max_in_flight > 1
    job.cancel()
    await job.wait()
    assert job.state == "cancelled" and job.count("cancelled") > 0
    assert job.finished_count == job.total
    await s.aclose()


async def test_errors_are_per_note_and_retry_works(vault):
    llm = MockLLM(fail_every=1)                                  # every call fails
    s = Session(vault, llm=llm); s.scan(); s.settings.workers = 2
    job = s.run_ai(route=False)
    await job.wait()
    assert job.count("error") == len(job.items) and job.count("done") == 0
    assert not s.stage.all(kind="header", source="ai") or all(c.data["fields"].keys() <= {"date"} for c in s.stage.all(kind="header"))
    llm.fail_every = 0
    j2 = s.run_ai(route=False, only_unstaged=True)
    await j2.wait()
    assert j2.count("error") == 0
    assert all(s.stage.get(rel, "header") for rel in job.items)
    await s.aclose()


async def test_stale_note_is_reanalysed(vault):
    llm = MockLLM()
    s = Session(vault, llm=llm); s.scan(); s.settings.workers = 1
    target = vault / "Zettelkasten/idea.md"
    state = {"n": 0}

    def hook(system, user):
        if "title: idea" in user and state["n"] == 0:
            state["n"] = 1
            target.write_text("Totally different new content about neural networks and attention.\n")
    llm.hook = hook
    job = s.run_ai(["Zettelkasten/idea.md"], route=False)
    await job.wait()
    assert job.items["Zettelkasten/idea.md"].state == "done"
    assert llm.calls == 2
    await s.aclose()


async def test_auto_level_accepts_only_confident(session):
    setup_plan(session)
    session.settings.auto_level = "auto"
    session.settings.auto_accept_threshold = 0.85
    await session.run_ai().wait()
    acc = session.stage.all(status="accepted")
    prop = session.stage.all(status="proposed")
    assert acc and prop
    assert all((c.confidence is None or c.confidence >= 0.85) for c in acc)
    assert all((c.confidence is not None and c.confidence < 0.85) for c in prop)


async def test_assist_level_never_accepts(session):
    setup_plan(session)
    session.settings.auto_level = "assist"
    await session.run_ai().wait()
    assert not session.stage.all(status="accepted")


async def test_auto_apply_respects_cap(vault):
    s = Session(vault, llm=MockLLM()); s.scan()
    s.settings.auto_level, s.settings.auto_apply, s.settings.auto_apply_cap = "auto", True, 3
    events = []
    s.on(lambda e, p: events.append(e))
    await s.run_ai(route=False).wait()
    await asyncio.sleep(0)
    assert "confirm_needed" in events
    assert s.stage.all(status="accepted")                         # nothing written without confirmation
    assert not s.batches()
    await s.aclose()


async def test_locked_and_no_ai_notes_are_skipped(session, llm):
    session.toggle_lock("Zettelkasten/idea.md")
    session.settings.no_ai_globs = ["Inbox/**"]
    job = session.run_ai(route=False)
    await job.wait()
    assert "Zettelkasten/idea.md" not in job.items and "Inbox/Link note.md" not in job.items


async def test_frozen_plan_collects_suggestions_and_accepting_them_stages_moves(session):
    setup_plan(session)
    session.set_frozen(True)
    session.taxonomy.folders.pop("3_University")
    n = session.index.get("Zettelkasten/Homework 3.md")
    from vaultcore.analyze import NoteAnalysis, WorkItem
    session._stage_analysis(NoteAnalysis(n.rel, folder=None, new_folder="3_University/Homework", reason="homework"),
                            WorkItem(n.rel, (), True))
    assert "3_University/Homework" in session.taxonomy.suggestions
    assert session.triage
    moved = session.accept_suggestion("3_University/Homework")
    assert moved == 1 and "3_University/Homework" in session.taxonomy
    assert session.stage.get(n.rel, "move").data["folder"] == "3_University/Homework"
    assert n.rel not in session.triage


async def test_rename_folder_retargets_stage_rules_and_instructions(session):
    setup_plan(session)
    session.add_rule("title~homework -> move:3_University")
    session.add_instruction("x", scope="folder", value="3_University", rerun=False)
    assert session.stage.get("Zettelkasten/Homework 3.md", "move").data["folder"] == "3_University"
    session.stage_move("Inbox/Link note.md", "3_University", source="manual")
    mapping = session.rename_folder("3_University", "3_Uni")
    assert mapping == {"3_University": "3_Uni"}
    assert session.stage.get("Zettelkasten/Homework 3.md", "move").data["folder"] == "3_Uni"
    assert session.stage.get("Inbox/Link note.md", "move").data["folder"] == "3_Uni"
    assert "move:3_Uni" in session.rules.rules[0].text
    assert session.instructions.items[0].value == "3_Uni"


async def test_rules_stage_changes_and_rejection_survives_reapply(session):
    session.add_rule('title~homework -> move:3_Uni tag+:School')
    mv = session.stage.get("Zettelkasten/Homework 3.md", "move")
    assert mv.source == "rule" and mv.status == "accepted"
    hd = session.stage.get("Zettelkasten/Homework 3.md", "header")
    assert hd.data["fields"]["tags"] == ["School"]
    session.reject([mv.id])
    session.apply_rules(None)
    assert session.stage.get("Zettelkasten/Homework 3.md", "move").status == "rejected"
    session.toggle_rule(session.rules.rules[0].id)               # disable -> changes vanish
    assert session.stage.get("Zettelkasten/Homework 3.md", "move") is None


async def test_manual_edit_beats_ai_and_rules(session):
    setup_plan(session)
    rel = "Zettelkasten/Homework 3.md"
    session.stage_move(rel, "4_Personal", source="manual")
    session.add_rule("title~homework -> move:3_University")
    assert session.stage.get(rel, "move").data["folder"] == "4_Personal"
    assert session.plan_work([rel], header=False, route=True, only_unstaged=False) == []   # manual pin: AI skips it
    assert session.stage.get(rel, "move").data["folder"] == "4_Personal"


async def test_rule_skip_and_lock_actions(session):
    session.add_rule("path:Inbox/** -> skip")
    session.add_rule("title:Complete -> lock")
    assert session.no_ai("Inbox/Link note.md")
    assert session.is_locked("Zettelkasten/Complete header.md")
    job = session.run_ai(route=False)
    assert "Inbox/Link note.md" not in job.items
    await job.wait()


async def test_tag_rename_merge_delete(session):
    for rel, tags in [("Zettelkasten/idea.md", ["LLMs", "AI"]), ("Zettelkasten/Attention.md", ["llm", "Attention"]),
                      ("Inbox/Link note.md", ["LLM"])]:
        session.stage_header(rel, {"tags": tags})
    session.apply()
    usage = dict(session.value_usage("tags"))
    assert usage.get("LLMs") == 1
    groups = session.value_groups("tags")
    assert any({"LLMs", "llm", "LLM"} <= {v for v in g} or len(g) >= 2 for g in groups)
    n = session.rename_value("tags", ["LLMs", "llm"], "LLM")
    assert n == 2
    session.apply()
    assert dict(session.value_usage("tags"))["LLM"] == 4
    session.rename_value("tags", ["AI"], None)
    session.apply()
    assert "AI" not in dict(session.value_usage("tags"))


async def test_watcher_detects_external_edits_and_queues_ai(vault):
    llm = MockLLM()
    s = Session(vault, llm=llm); s.scan(); s.settings.auto_level = "assist"
    (vault / "Inbox/brand new.md").write_text("A brand new note about neural networks and attention. " * 5)
    d = s.check_disk()
    assert d.added == ["Inbox/brand new.md"]
    await asyncio.sleep(0.05)
    assert s.jobs.jobs
    await s.jobs.jobs[-1].wait()
    assert s.stage.get("Inbox/brand new.md", "header")
    s.settings.auto_level = "manual"
    (vault / "Inbox/another.md").write_text("another")
    n_jobs = len(s.jobs.jobs)
    s.check_disk()
    assert len(s.jobs.jobs) == n_jobs and s.index.get("Inbox/another.md")
    await s.aclose()


async def test_stage_survives_restart(vault, llm):
    s = Session(vault, llm=llm); s.scan()
    s.stage_move("Inbox/Link note.md", "X")
    s.taxonomy.add("X"); s.save_all()
    await s.aclose()
    s2 = Session(vault, llm=llm); s2.scan()
    assert s2.stage.get("Inbox/Link note.md", "move").data["folder"] == "X" and "X" in s2.taxonomy
    await s2.aclose()


async def test_propose_structure_and_translate_instruction(session):
    folders, note = await session.propose_structure("keep ML in research")
    assert folders and all(f.path for f in folders)
    session.adopt_structure(folders)
    assert "2_Research/AI" in session.taxonomy
    rules, expl = await session.translate_instruction("keep notes about calculus in 3_University")
    assert rules and rules[0].endswith("move:3_University")


async def test_header_schema_is_configurable(vault, llm):
    from vaultcore.settings import FieldSpec
    s = Session(vault, llm=llm)
    s.settings.header_schema = [FieldSpec("type", "text", instruction="note type", allowed=["Idea", "Reference"]),
                                FieldSpec("topics", "list", min_items=1, instruction="topics"),
                                FieldSpec("created", "date", source="created", date_format="%Y-%m-%d")]
    s.scan()
    await s.run_ai(["Zettelkasten/idea.md"], route=False).wait()
    c = s.stage.get("Zettelkasten/idea.md", "header")
    assert set(c.data["fields"]) == {"type", "topics", "created"}
    assert c.data["fields"]["type"] in ("Idea", "Reference")
    s.accept([c.id]); s.apply()
    text = (vault / "Zettelkasten/idea.md").read_text()
    assert text.startswith("---\ntype: ") and "topics: [" in text and "created: 20" in text
    await s.aclose()
