"""A fake LLM for demos and tests (``--mock``): no server needed, deterministic, understands
the prompts built in ``prompts.py``. It is *not* smart — it just exercises the whole pipeline."""
from __future__ import annotations

import asyncio
import json
import random
import re
from collections import Counter

from .llm import LLMResponse
from .settings import LLMProfile

_STOP = set("""the a an and or but of to in on for with is are was were be been it this that these those as at by from
not no yes if then than so such can will would should could may might do does did have has had i you he she we they my
our your their its into about over under more most some any all each other which what when where who how also just
only very one two use used using note notes""".split())


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", text) if w.lower() not in _STOP]


class MockLLM:
    def __init__(self, delay: float = 0.0, fail_every: int = 0, jitter: float = 0.0):
        self.profile = LLMProfile(name="mock", base_url="mock://", model="mock-1")
        self.delay = delay
        self.jitter = jitter
        self.fail_every = fail_every
        self.calls = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self.prompts: list[tuple[str, str]] = []
        self.hook = None          # optional callable(system, user) run at the start of every call

    def note_empty_answer(self) -> None:  # parity with LLMClient
        return None

    async def aclose(self) -> None:  # parity with LLMClient
        return None

    async def list_models(self) -> list[str]:
        return ["mock-1"]

    async def chat(self, system: str, user: str, *, json_mode: bool = False, max_tokens: int | None = None) -> LLMResponse:
        self.calls += 1
        self.prompts.append((system, user))
        if self.hook:
            self.hook(system, user)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay or self.jitter:
                await asyncio.sleep(self.delay + random.random() * self.jitter)
            if self.fail_every and self.calls % self.fail_every == 0:
                from .llm import LLMError
                raise LLMError("mock failure", retryable=True)
            if '"results"' in system:
                out = self._analyze(system, user)
            elif '"folders"' in system:
                out = self._structure(user)
            elif '"rules"' in system:
                out = self._rules(user)
            else:
                out = {}
            text = json.dumps(out)
            return LLMResponse(text, prompt_tokens=len(user) // 4, completion_tokens=len(text) // 4, latency=self.delay)
        finally:
            self.in_flight -= 1

    # ---- analyze -------------------------------------------------------------------
    def _analyze(self, system: str, user: str) -> dict:
        fields = re.findall(r'^  - "(\w[\w\- ]*)": (\["\.\.\."|")', system, re.M)
        want_route = '"folder": "<one of' in system
        folders: list[tuple[str, str]] = []
        m = re.search(r"FOLDERS:\n(.*?)(?:\n\n|$)", user, re.S)
        if m:
            for line in m.group(1).splitlines():
                mm = re.match(r"- ([^:]+?)(?::\s*(.*))?$", line)
                if mm:
                    folders.append((mm.group(1).strip(), mm.group(2) or ""))
        results = []
        for nm in re.finditer(r"NOTE \[id=(\S+?)\] title: (.*?)\n(.*?)<<<\n(.*?)\n>>>", user, re.S):
            nid, title, _, text = nm.groups()
            words = _words(text + " " + title)
            common = [w.title() for w, _ in Counter(w.lower() for w in words).most_common(8)]
            res: dict = {"id": nid, "confidence": {}}
            if fields:
                f: dict = {}
                for key, kind in fields:
                    if kind.startswith("["):
                        f[key] = common[:5] or ["Misc"]
                    elif key == "parent":
                        f[key] = f"{common[0] if common else 'Fleeting'} MOC"
                    elif key == "status":
                        f[key] = "Draft" if len(text.split()) > 60 else "Idea"
                    else:
                        f[key] = common[0] if common else "Unknown"
                res["fields"] = f
                res["confidence"]["fields"] = 0.9 if len(text.split()) > 25 else 0.4
            if want_route:
                low = text.lower() + " " + title.lower()
                best, score = "", 0
                for path, desc in folders:
                    toks = [t for t in re.split(r"[\W_]+", (path + " " + desc).lower()) if len(t) > 3]
                    s = sum(low.count(t) for t in toks)
                    if s > score:
                        best, score = path, s
                res["folder"] = best
                res["new_folder"] = ""
                if not best and "homework" in low and folders:
                    res["new_folder"] = folders[0][0].split("/")[0] + "/Homework"
                res["confidence"]["folder"] = min(0.95, 0.5 + 0.1 * score) if best else 0.2
            res["reason"] = "mock heuristic"
            results.append(res)
        return {"results": results}

    # ---- structure / rules ---------------------------------------------------------
    def _structure(self, user: str) -> dict:
        return {"folders": [
            {"path": "1_Zettelkasten", "description": "atomic evergreen notes, ideas and fleeting thoughts"},
            {"path": "2_Research", "description": "papers, research reading and technical studies"},
            {"path": "2_Research/AI", "description": "machine learning, neural networks, transformers, LLM"},
            {"path": "3_University", "description": "course notes, lectures, exams and coursework"},
            {"path": "4_Personal", "description": "journal, personal life, recipes and plans"},
        ], "notes_to_user": "mock structure"}

    def _rules(self, user: str) -> dict:
        m = re.search(r"INSTRUCTION:\n(.*?)\n\nRespond", user, re.S)
        ins = (m.group(1) if m else "").strip()
        mm = re.search(r"(?:about|on|containing)\s+([\w\- ]+?)\s+(?:in|under|into|to)\s+(\S+)", ins, re.I)
        if mm:
            kw, folder = mm.group(1).strip(), mm.group(2).strip(".,")
            return {"rules": [f'text~"{kw}" -> move:{folder}'], "explanation": "mock translation"}
        return {"rules": [], "explanation": "mock could not translate"}
