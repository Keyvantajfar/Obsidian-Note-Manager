import random

from vaultcore import frontmatter as fm
from vaultcore.settings import default_schema

S = default_schema()


def test_round_trip_exact():
    for t in ["", "no header\n", "---\na: b\n---\nbody", "---\r\na: b\r\n---\r\n\r\nx\r\n",
              "﻿---\na: b\n---\nx", "---\n---\n", "---\nunterminated\nbody", "--- not fm\nx"]:
        assert fm.render(fm.parse(t)) == t


def test_body_never_changes_fuzz():
    rnd = random.Random(7)
    pieces = ["\n", "\r\n", " ", "text", "---", "# h", "\t", "[[x]]", "tags: a", "\n\n"]
    for _ in range(300):
        body = "".join(rnd.choice(pieces) for _ in range(rnd.randint(0, 30)))
        header = rnd.choice(["", "---\nparent: x\n---\n", "---\r\ntags: [[A]]\r\n---\r\n"])
        text = header + body
        new = fm.apply_fields(text, S, {"parent": "P", "status": "Idea", "tags": ["a"], "date": "2020-01-01T00:00"})
        assert fm.body_of(new) == fm.body_of(text)


def test_merge_keeps_existing_lines_byte_for_byte():
    t = '---\nparent: "[[X MOC]]"\ncustom: keep   me\ntags: [[A]],[[B]]\n---\nbody\n'
    new = fm.apply_fields(t, S, {"parent": "Other", "status": "Idea", "tags": ["z"], "date": "2020-01-01T00:00"})
    assert 'parent: "[[X MOC]]"\ncustom: keep   me\ntags: [[A]],[[B]]\n' in new
    assert "status: #Idea\n" in new and "date: 2020-01-01T00:00\n" in new
    assert "Other" not in new and "[[z]]" not in new


def test_overwrite_only_named_key():
    t = '---\nparent: "[[X]]"\ntags: [[A]], [[B]]\n---\nb'
    new = fm.apply_fields(t, S, {"tags": ["A", "C"]}, overwrite=["tags"])
    assert 'parent: "[[X]]"\n' in new and "tags: [[A]], [[C]]\n" in new


def test_new_header_and_newline_style():
    assert fm.apply_fields("hello\n", S, {"status": "Idea"}) == "---\nstatus: #Idea\n---\nhello\n"
    crlf = fm.apply_fields("hello\r\nworld\r\n", S, {"status": "Idea"})
    assert crlf == "---\r\nstatus: #Idea\r\n---\r\nhello\r\nworld\r\n"
    assert fm.apply_fields("﻿x\n", S, {"status": "Idea"}).startswith("﻿---\n")


def test_crlf_header_detected_not_duplicated():
    t = "---\r\ntags: [[AI]]\r\n---\r\nbody"
    assert fm.parse(t).has_fm
    assert fm.missing_keys(t, S) == ["parent", "status", "date"]


def test_empty_value_gets_filled_and_block_lists_count_as_value():
    t = "---\nparent:\ntags:\n  - a\n  - b\n---\nx"
    assert fm.missing_keys(t, S) == ["parent", "status", "date"]
    new = fm.apply_fields(t, S, {"parent": "M", "tags": ["z"]})
    assert 'parent: "[[M]]"' in new and "  - a\n  - b\n" in new and "[[z]]" not in new
    assert fm.header_values(t, S)["tags"] == ["a", "b"]


def test_to_list_formats():
    assert fm.to_list("[[A]], [[B|alias]]") == ["A", "B"]
    assert fm.to_list("[a, b]") == ["a", "b"]
    assert fm.to_list("a, b , a") == ["a", "b"]
    assert fm.to_list("#a #b") == ["a", "b"]
    assert fm.to_list('"[[X]]"') == ["X"]
    assert fm.to_list("") == []


def test_status_hash_is_not_a_yaml_comment():
    t = "---\nstatus: #Idea\n---\n"
    assert fm.header_values(t, S)["status"] == ["Idea"]


def test_text_values_are_yaml_safe():
    from vaultcore.settings import FieldSpec
    line = fm.format_line(FieldSpec("title", "text"), "title", "a: b")
    assert line == 'title: "a: b"'


def test_header_diff():
    old = "---\ntags: [[A]]\n---\nb"
    new = fm.apply_fields(old, S, {"status": "Idea"})
    d = fm.header_diff(old, new)
    assert ("+", "status: #Idea") in d and (" ", "tags: [[A]]") in d


def test_header_after_leading_blank_line_is_recognised_not_duplicated():
    from vaultcore.frontmatter import apply_fields, parse
    from vaultcore.settings import default_schema
    text = '\n---\nparent: "[[Fleeting MOC]]"\nstatus:\ntags: [[A]], [[]]\ndate: 2024-09-16T17:18\n\n---\n# Title\n\nbody\n'
    p = parse(text)
    assert p.has_fm and p.lead == "\n" and p.body == "# Title\n\nbody\n"
    out = apply_fields(text, default_schema(), {"status": "#Idea"})
    assert out.startswith("---\n") and out.count("---\n") == 2          # one header, now on line 1
    assert "status: #Idea" in out and out.endswith("# Title\n\nbody\n")
    assert "tags: [[A]], [[]]" in out                                     # untouched
