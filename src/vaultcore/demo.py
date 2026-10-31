"""Create a small messy sample vault (for ``vaultmgr --demo`` and the tests)."""
from __future__ import annotations

import os
from pathlib import Path

_AI = ("Attention mechanism in transformers. Self-attention computes query key value projections; "
       "multi-head attention lets the model attend to different positions. Neural network training with "
       "backpropagation and gradient descent. Transformer models and LLM scaling.")
_MATH = ("Fourier transform decomposes a signal into frequencies. Calculus: integrals, derivatives and series. "
         "Linear algebra eigenvalues and eigenvectors. Proof by induction.")
_UNI = ("Lecture notes for the university course on statistics: exam topics, homework problems and lecture slides. "
        "Course schedule, professor office hours, exam preparation.")
_HW = "Homework 3 for the statistics course: problems 1-5, due Friday. Homework solutions and homework review."
_PERSONAL = ("Journal entry: went for a walk, thinking about life plans, family dinner and personal goals. "
             "Recipe: pasta with tomato and basil, cooking notes.")

NOTES: dict[str, str] = {
    "Zettelkasten/Attention.md": _AI,
    "Zettelkasten/Backprop.md": "Backpropagation and gradient descent for neural network training. " + _AI,
    "Zettelkasten/Fourier.md": _MATH,
    "Zettelkasten/Calculus basics.md": _MATH + " More calculus: limits and series.",
    "Zettelkasten/Lecture 1 statistics.md": _UNI,
    "Zettelkasten/Homework 3.md": _HW,
    "Zettelkasten/Homework 4.md": _HW + " Extra homework problems.",
    "Zettelkasten/Journal 2024-01-02.md": _PERSONAL,
    "Zettelkasten/Pasta recipe.md": "Recipe: pasta with tomato and basil. Cooking notes, kitchen and food.",
    "Zettelkasten/idea.md": "Quick idea.",
    "Research/Transformers paper.md": "Paper review: attention is all you need. " + _AI,
    "Research/Diffusion models.md": "Diffusion models for image generation, neural network denoising. " + _AI,
    "Research/Personal reading list.md": "Books to read this year: personal goals and journal ideas. " + _PERSONAL,
    "University/Statistics exam.md": _UNI + " Exam on statistics and probability.",
    "University/Linear algebra.md": _MATH + " University linear algebra course lecture.",
    "University/Notes.md": "University generic notes. " + _UNI,
    "Research/Notes.md": "Research generic notes about neural network papers. " + _AI,
    "Inbox/Unsorted thought.md": "Something vague without much content.",
    "Inbox/Link note.md": "See [[Zettelkasten/Attention]] and [[Fourier]] for details. Also ![[diagram.png]] and [pic](img/a.png).",
}

# notes with special header/newline situations
SPECIAL: dict[str, str] = {
    "Zettelkasten/Complete header.md": '---\nparent: "[[Deep Learning MOC]]"\nstatus: #Permanent\ntags: [[LLM]], [[Transformers]], [[Attention]]\ndate: 2024-03-01T10:00\n---\n\nAlready tagged note about transformers.\n',
    "Zettelkasten/Partial header.md": '---\ntags: [[Math]], [[Calculus]]\ncustom: keep me\n---\nCalculus note with a partial header: integrals and derivatives, series and limits.\n',
    "Zettelkasten/Windows note.md": '\r\n# Windows note\r\n\r\nWritten on Windows with CRLF line endings: neural network attention.\r\n',
    "Zettelkasten/CRLF header.md": '---\r\ntags: [[AI]]\r\n---\r\n\r\nBody with CRLF and a header about neural networks.\r\n',
}


def make_demo_vault(path: str | Path, *, special: bool = True) -> Path:
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    (root / ".obsidian").mkdir(exist_ok=True)
    (root / ".obsidian" / "app.json").write_text("{}")
    items = dict(NOTES)
    if special:
        items.update(SPECIAL)
    for rel, text in items.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text if text.endswith(("\n", "\r\n")) or "\r\n" in text else text + "\n")
    return root
