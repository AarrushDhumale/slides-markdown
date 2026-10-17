"""Pull the closing "Check your understanding" quizzes out of an assembled doc (no model call).

Writers use two layouts, both handled:
  1. Q        2. Q          (questions, then one <details> holding numbered answers)
  <details> 1. A  2. A </details>
and
  1. Q <details>A</details>   2. Q <details>A</details>   (an answer after each question)
"""

from __future__ import annotations

import re

QUIZ_HEADING_RE = re.compile(r"^#{2,6}\s+Check your understanding\s*$", re.I | re.M)
CHAPTER_RE = re.compile(r"^## (?!Contents\b)(.+)$", re.M)
DETAILS_RE = re.compile(r"<details>\s*<summary>.*?</summary>(.*?)</details>", re.S | re.I)
NUMBERED_RE = re.compile(r"^\s*(\d+)\.\s+(.*)$")


def _block_after(md: str, start: int) -> str:
    """The quiz body: up to the next heading or horizontal rule."""
    rest = md[start:]
    m = re.search(r"^(#{1,6}\s|---\s*$)", rest, flags=re.M)
    return rest[:m.start()] if m else rest


def _numbered(text: str) -> list[str]:
    """Split '1. a\\n   more\\n2. b' into ['a more', 'b'] (continuation lines are joined)."""
    items: list[str] = []
    for line in text.strip().splitlines():
        if m := NUMBERED_RE.match(line):
            items.append(m.group(2).strip())
        elif items and line.strip():
            items[-1] += "\n" + line.strip()
    return items


def parse_quiz_block(block: str) -> list[dict]:
    answers = [a.strip() for a in DETAILS_RE.findall(block)]
    questions = _numbered(DETAILS_RE.sub("", block))
    if not questions:
        return []
    if len(answers) == len(questions):
        return [{"q": q, "a": a} for q, a in zip(questions, answers)]
    if len(answers) == 1:
        split = _numbered(answers[0])
        if len(split) == len(questions):
            return [{"q": q, "a": a} for q, a in zip(questions, split)]
        return [{"q": "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1)), "a": answers[0]}]
    return [{"q": q, "a": answers[i] if i < len(answers) else ""} for i, q in enumerate(questions)]


def extract_quiz(md: str) -> list[dict]:
    """[{chapter, questions: [{q, a}]}] in document order; chapters without a quiz are skipped."""
    chapters = [(m.start(), m.group(1).strip()) for m in CHAPTER_RE.finditer(md)]
    out: list[dict] = []
    for m in QUIZ_HEADING_RE.finditer(md):
        chapter = next((title for pos, title in reversed(chapters) if pos < m.start()), "Quiz")
        questions = parse_quiz_block(_block_after(md, m.end()))
        if not questions:
            continue
        if out and out[-1]["chapter"] == chapter:
            out[-1]["questions"] += questions
        else:
            out.append({"chapter": chapter, "questions": questions})
    return out
