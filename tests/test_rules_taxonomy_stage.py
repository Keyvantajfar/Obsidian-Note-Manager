import pytest

from vaultcore.index import VaultIndex
from vaultcore.rules import RuleError, RuleSet, parse_rule
from vaultcore.settings import Settings
from vaultcore.stage import Change, Stage
from vaultcore.taxonomy import Taxonomy


def idx(vault):
    i = VaultIndex(vault, Settings())
    i.scan()
    return i


def test_rule_parse_errors():
    for bad in ["", "tag:x", "-> move:a", "tag:x ->", "tag:x -> bogus:1", "title~( -> skip", "path~x -> skip",
                "tag:x -> move:../etc", "tag:x -> set:novalue"]:
        with pytest.raises(RuleError):
            parse_rule(bad)


def test_rule_evaluation(vault):
    i = idx(vault)
    rs = RuleSet(vault)
    rs.add('title~homework -> move:3_Uni/Homework')
    rs.add('path:Inbox/** -> skip')
    rs.add('noheader text~"neural network" -> tag+:AI set:status=Idea')
    rs.add('tag:AI folder:Zettelkasten -> move:never')       # tag not present -> no match
    n = i.get("Zettelkasten/Homework 3.md")
    out = rs.evaluate(n, lambda: i.text(n.rel))
    assert out.move == "3_Uni/Homework" and not out.skip
    out = rs.evaluate(i.get("Inbox/Unsorted thought.md"), lambda: "")
    assert out.skip
    a = i.get("Zettelkasten/Attention.md")
    out = rs.evaluate(a, lambda: i.text(a.rel))
    assert out.adds == {"tags": ["AI"]} and out.sets["status"] == ("Idea", False)
    assert out.move is None


def test_rule_quoting_negation_and_header_conditions(vault):
    i = idx(vault)
    rs = RuleSet(vault)
    rs.add('"parent:Deep Learning MOC" -> lock')
    rs.add('!noheader -> skip')
    c = i.get("Zettelkasten/Complete header.md")
    out = rs.evaluate(c, lambda: "")
    assert out.lock and out.skip


def test_rules_persist_and_flag_invalid(vault):
    rs = RuleSet(vault)
    rs.add("tag:x -> skip")
    rs.save()
    (vault / ".vaultmgr" / "rules.json").write_text('[{"id":"a","text":"garbage"},{"id":"b","text":"tag:x -> skip"}]')
    rs2 = RuleSet(vault)
    assert rs2.rules[0].error and not rs2.rules[0].enabled and rs2.rules[1].enabled


def test_taxonomy_basics(tmp_path):
    t = Taxonomy(tmp_path)
    t.add("2_Research/AI", "ml")
    assert "2_Research" in t and t.children("2_Research") == ["2_Research/AI"]
    assert t.match("ai") == "2_Research/AI" and t.match("2_research/ai") == "2_Research/AI"
    assert t.match("nonsense folder") is None
    with pytest.raises(ValueError):
        t.add("../x")
    with pytest.raises(ValueError):
        t.add(".hidden")
    t.save()
    assert Taxonomy(tmp_path).folders["2_Research/AI"].description == "ml"


def test_taxonomy_rename_subtree_and_suggestions(tmp_path):
    t = Taxonomy(tmp_path)
    t.add("A/B/C")
    t.add("A/B")
    m = t.rename("A/B", "X/Y")
    assert m == {"A/B": "X/Y", "A/B/C": "X/Y/C"}
    assert {"A", "X", "X/Y", "X/Y/C"} <= set(t.folders)
    with pytest.raises(ValueError):
        t.rename("X", "X/Z")
    t.suggest("A/New", "n.md")
    t.suggest("A/New", "m.md")
    assert t.suggestions["A/New"].notes == ["n.md", "m.md"]
    t.add("A/New")
    assert "A/New" not in t.suggestions


def test_stage_priority_and_persistence(tmp_path):
    st = Stage(tmp_path)
    ai = Change("1", "move", "a.md", "ai", data={"folder": "X"})
    man = Change("2", "move", "a.md", "manual", data={"folder": "Y"})
    assert st.upsert(ai)
    assert st.upsert(man)
    assert st.get("a.md", "move").data["folder"] == "Y"
    assert st.upsert(Change("3", "move", "a.md", "ai", data={"folder": "Z"})) is None     # lower priority ignored
    assert st.get("a.md", "move").data["folder"] == "Y"
    st.flush()
    assert Stage(tmp_path).get("a.md", "move").source == "manual"
    st.rename_note("a.md", "b.md")
    assert st.get("b.md", "move") and st.get("a.md", "move") is None
