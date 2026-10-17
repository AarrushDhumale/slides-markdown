"""Detect the course's chapters in a sequence of slides (one combined PDF, several lecture PDFs, ...).

Chapters are the top-level partition of the main study doc. Detection is one model call over a compact
slide listing; the result is then made valid by code: contiguous, in order, covering every slide, with
tiny "chapters" merged into a neighbour. Small single-topic uploads skip the call entirely.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from backend.llm.client import LLM
from backend.llm.usage import BudgetExceeded
from backend.paths import PROMPTS_DIR
from backend.pipeline.chapter import SEP, Chapter, Slide
from backend.pipeline.extract import SlideExtract
from backend.pipeline.outline import FILLER_ROLES

SINGLE_TOPIC_MAX = 14      # this few teaching slides: always one chapter, no detection call
MIN_CHAPTER_SLIDES = 6     # detected chapters with fewer teaching slides are merged into a neighbour


class ChapterStart(BaseModel):
    title: str
    first_slide: str = Field(description="id of the chapter's first slide, e.g. 'A·1' or 'C·14'")


class ChapterSplit(BaseModel):
    chapters: list[ChapterStart] = []


def _listing(slides: list[Slide], extracts: dict[str, SlideExtract]) -> str:
    lines, current_pdf = [], None
    for s in slides:
        if s.pdf.sha != current_pdf:
            current_pdf = s.pdf.sha
            lines.append(f"=== PDF {s.id.split(SEP)[0]}: {Path(s.pdf.source).name} ===")
        ex = extracts.get(s.id)
        if ex is None:
            lines.append(f"{s.id} [repeat of {s.duplicate_of}]")
            continue
        first = next((b.strip("- ").strip() for b in ex.bullets if b.strip()), "")
        lines.append(f"{s.id} [{ex.slide_role}] {ex.title.strip()}" + (f" | {first[:90]}" if first else ""))
    return "\n".join(lines)


def _teaching(group: list[Slide], extracts: dict[str, SlideExtract]) -> int:
    return sum(1 for s in group if s.id in extracts and extracts[s.id].slide_role not in FILLER_ROLES)


def default_title(slides: list[Slide], extracts: dict[str, SlideExtract]) -> str:
    for s in slides:
        ex = extracts.get(s.id)
        if ex and ex.slide_role == "title" and ex.title.strip():
            return ex.title.strip()
    return slides[0].pdf.title if slides else "Notes"


def _join_titles(a: str, b: str) -> str:
    if not _teaching_title(b) or _title_key(b) in _title_key(a):
        return a
    return f"{a} and {b}"


def _teaching_title(t: str) -> bool:
    return bool(t.strip()) and _title_key(t) not in ("chapter", "notes", "introduction")


def _title_key(title: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", title.lower()).split())


def _opens(prev: Slide, start: Slide, extracts: dict[str, SlideExtract]) -> bool:
    """Does `prev` belong to the opening of the chapter that starts at `start`?"""
    if prev.page == 1 and prev.pdf.sha != getattr(start, "pdf", prev.pdf).sha:
        return False  # first page of a *different* PDF than the start: leave it alone
    ex = extracts.get(prev.id)
    if ex is None:                                   # recap repeat of an earlier slide
        return prev.pdf.sha == start.pdf.sha
    return ex.slide_role in ("title", "agenda") or (prev.page == 1 and prev.pdf.sha == start.pdf.sha)


def normalise(starts: list[ChapterStart], slides: list[Slide],
              extracts: dict[str, SlideExtract]) -> list[tuple[str, list[Slide]]]:
    """Turn model output into contiguous, ordered, non-tiny chapters covering every slide."""
    pos = {s.id: i for i, s in enumerate(slides)}
    valid = sorted({pos[c.first_slide.strip()]: c.title.strip() or "Chapter"
                    for c in starts if c.first_slide.strip() in pos}.items())
    if not valid:
        valid = [(0, default_title(slides, extracts))]
    elif valid[0][0] != 0:  # slides before the first detected start belong to the first chapter
        valid[0] = (0, valid[0][1])
    # a chapter starts at its opening slides: pull each start back over a lecture's title/agenda
    # slides, a PDF's first page and recap repeats (models tend to start at the first content slide)
    snapped = []
    for p, title in valid:
        while p > 0 and (p - 1) not in {q for q, _ in snapped} and _opens(slides[p - 1], slides[p], extracts):
            p -= 1
        snapped.append((p, title))
    valid = sorted(dict(snapped).items())
    bounds = [p for p, _ in valid] + [len(slides)]
    chapters = [(title, slides[bounds[k]:bounds[k + 1]]) for k, (_, title) in enumerate(valid)]
    # neighbouring chapters the model gave the same title are one chapter
    merged: list[tuple[str, list[Slide]]] = []
    for title, group in chapters:
        if merged and _title_key(merged[-1][0]) == _title_key(title):
            merged[-1] = (merged[-1][0], merged[-1][1] + group)
        else:
            merged.append((title, group))
    chapters = merged
    # merge chapters that are too small to stand alone into the previous one (or the next, if first)
    k = 0
    while len(chapters) > 1 and k < len(chapters):
        title, group = chapters[k]
        if _teaching(group, extracts) < MIN_CHAPTER_SLIDES:
            # keep the small chapter's subject in the title so it isn't written away
            if k == 0:
                chapters[1] = (_join_titles(title, chapters[1][0]), group + chapters[1][1])
            else:
                chapters[k - 1] = (_join_titles(chapters[k - 1][0], title), chapters[k - 1][1] + group)
            del chapters[k]
            continue
        k += 1
    return chapters


def detect_chapters(chapter: Chapter, slides: list[Slide], extracts: dict[str, SlideExtract],
                    llm: LLM) -> list[tuple[str, list[Slide]]]:
    if _teaching(slides, extracts) <= SINGLE_TOPIC_MAX:
        return [(default_title(slides, extracts), slides)]
    prompt = (PROMPTS_DIR / "chapters.md").read_text()
    user = (f"Total teaching slides: {_teaching(slides, extracts)}\n\nSlides:\n{_listing(slides, extracts)}")
    key = hashlib.sha256(f"{prompt}\n{user}\n{llm.model_id}".encode()).hexdigest()[:16]
    path = chapter.dir / "chapters.json"
    starts: list[ChapterStart] | None = None
    if path.exists():
        cached = json.loads(path.read_text())
        if cached.get("key") == key:
            starts = [ChapterStart(**c) for c in cached["chapters"]]
    if starts is None:
        try:
            llm.connect()
            starts = llm.structured([{"role": "system", "content": prompt}, {"role": "user", "content": user}],
                                    ChapterSplit, max_tokens=2048).chapters
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"key": key, "chapters": [c.model_dump() for c in starts]},
                                       indent=1, ensure_ascii=False))
        except BudgetExceeded:  # a spend cap stops the run; never degrade around it
            raise
        except Exception as e:  # fall back to one chapter per PDF
            print(f"\n  ! chapter detection failed ({type(e).__name__}); using one chapter per PDF")
            starts = [ChapterStart(title=s.pdf.title, first_slide=s.id) for s in slides if s.page == 1]
    return normalise(starts, slides, extracts)
