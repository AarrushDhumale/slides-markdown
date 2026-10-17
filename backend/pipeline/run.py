"""End-to-end orchestration shared by the CLI and (later) the web app."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from backend.llm.client import stage_llm
from backend.pipeline.assemble import assemble
from backend.pipeline.chapter import Chapter, build_slides
from backend.pipeline.extract import extract_all
from backend.pipeline.outline import MODE_SUFFIX, build_outline
from backend.pipeline.chapterize import detect_chapters
from backend.pipeline.per_deck import build_per_chapter_outline, build_per_deck_outline
from backend.pipeline.per_slide import build_per_slide_outline
from backend.pipeline.summarize import summarize
from backend.pipeline.write import write_all

Progress = Callable[[str, int, int], None]  # (stage, done, total)

# Topic layout: about one section per 5 teaching slides with no 12-section ceiling (a book, not a chapter)
# and a book-oriented prompt. The model always decides the topics (a single-topic upload comes back as one
# section); if its call fails, consecutive slides are grouped instead (outline.fallback_outline).
TOPIC_LAYOUT = dict(prompt_file="topics.md", per_section=5, min_sections=2, max_sections=80)


def save_slide_index(chapter: Chapter, slides, extracts) -> None:
    """extracts.json (what each slide says) + slides.json (its page image), for the web app's
    slide popovers, quiz and "Explain this" context."""
    chapter.dir.mkdir(parents=True, exist_ok=True)
    (chapter.dir / "extracts.json").write_text(json.dumps(
        {sid: ex.model_dump(mode="json") for sid, ex in extracts.items()}, ensure_ascii=False))
    (chapter.dir / "slides.json").write_text(json.dumps(
        {s.id: {"image": str(s.image_path), "duplicate_of": s.duplicate_of} for s in slides}, ensure_ascii=False))


def convert_chapter(chapter: Chapter, levels: list[str], backend: str | None = None,
                    progress: Progress | None = None, review: bool = True,
                    per_slide: bool = False, per_deck: bool = False, by_topic: bool = False) -> dict[str, Path]:
    """Default layout: chapter-wise (auto-detected chapters). Alternatives: per_deck, per_slide, by_topic."""
    if sum([per_slide, per_deck, by_topic]) > 1:
        raise ValueError("choose at most one of per_slide / per_deck / by_topic")
    def report(stage: str):
        return (lambda done, total: progress(stage, done, total)) if progress else None

    t0 = time.time()
    pdfs = chapter.ingest()
    slides = build_slides(pdfs)
    if per_slide:
        # every slide gets its own section, so an earlier "build" slide superseded by a fuller later
        # one must be read too (its overlap with the later slide is handled when writing)
        pos = {s.id: i for i, s in enumerate(slides)}
        for s in slides:
            if s.duplicate_of and pos[s.duplicate_of] > pos[s.id]:
                s.duplicate_of = None
    if progress:
        progress("ingest", len(slides), len(slides))

    extract_llm = stage_llm("extract", backend)
    extracts = extract_all(slides, extract_llm, report("extract"))
    save_slide_index(chapter, slides, extracts)

    outline_llm = stage_llm("outline", backend)
    if per_deck:  # one topic outline per deck; never merged across decks
        outline = build_per_deck_outline(chapter, pdfs, slides, extracts, outline_llm, extract_llm.model_id)
    elif by_topic:  # one flat book of topic sections, merged across every PDF
        outline = build_outline(chapter, slides, extracts, outline_llm, extract_llm.model_id, **TOPIC_LAYOUT)
    elif per_slide:
        outline = build_outline(chapter, slides, extracts, outline_llm, extract_llm.model_id)
    else:  # chapter-wise: detect the course's chapters, then lay each out as one continuous part
        detected = detect_chapters(chapter, slides, extracts, outline_llm)
        outline = build_per_chapter_outline(chapter, detected, extracts, outline_llm, extract_llm.model_id)
    if per_slide:  # topic outline only supplies the chapter title/overview here
        outline = build_per_slide_outline(outline.chapter_title, outline.overview, slides, extracts)
    if progress:
        progress("outline", len(outline.sections), len(outline.sections))

    write_llm = stage_llm("write", backend)
    outputs = {}
    written: dict[str, list[str]] = {}
    # the summary is condensed from the learner doc, so learner sections are needed (cached if present)
    to_write = [lv for lv in levels if lv != "summary"]
    if "summary" in levels and "learner" not in to_write:
        to_write.append("learner")
    for level in to_write:
        written[level] = write_all(outline, level, extracts, slides, write_llm, chapter,
                                   report(f"write:{level}"), review=review)
        if level in levels:
            outputs[level] = assemble(chapter, outline, written[level], level, slides, extracts)
            chapter.mark_generated(level + MODE_SUFFIX[outline.mode])
    if "summary" in levels:
        sections, takeaways = summarize(outline, written["learner"], write_llm, chapter, report("summary"))
        outputs["summary"] = assemble(chapter, outline, sections, "summary", slides, None, takeaways)
        chapter.mark_generated("summary" + MODE_SUFFIX[outline.mode])
    if progress:
        progress("done", int(time.time() - t0), 0)
    return outputs
