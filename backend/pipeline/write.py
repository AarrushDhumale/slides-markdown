"""Stage 4: write each outline section at the requested level (cached per section/level/model)."""

from __future__ import annotations

import hashlib
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from backend.llm.client import LLM, image_part
from backend.llm.usage import BudgetExceeded
from backend.paths import PROMPTS_DIR
from backend.pipeline.assemble import (check_markdown, degeneration, missing_slides, resolve_asset,
                                       strip_degenerate)
from backend.pipeline.chapter import Chapter, Slide, slide_file_id
from backend.pipeline.extract import SlideExtract, figure_assets
from backend.pipeline.outline import MODE_DIRS, Outline, Section

MAX_WRITER_IMAGES = 6
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(assets/[^)]+\)")
# target words per word of slide content; bounds keep tiny/huge sections sensible
LENGTH_FACTOR = {"proficient": 0.6, "learner": 1.1, "dockey": 1.3}
OVERSHOOT_LIMIT = 1.35    # sections longer than this x budget get one "tighten" pass
MIN_WORDS, MAX_WORDS = 120, 900                   # topic sections
SLIDE_MIN_WORDS, SLIDE_MAX_WORDS = 50, 400        # per-slide mode


def source_words(slide_ids: list[str], extracts: dict[str, SlideExtract]) -> int:
    n = 0
    for sid in slide_ids:
        ex = extracts[sid]
        n += sum(len(t.split()) for t in [ex.title, *ex.bullets, *ex.tables])
        n += 6 * len(ex.equations) + sum(3 * len(c.code.splitlines()) for c in ex.code)
        n += sum(len(f.description.split()) // 4 for f in ex.figures if f.importance != "decorative")
    return n


def target_words(level: str, slide_ids: list[str], extracts: dict[str, SlideExtract],
                 scale: float = 1.0, per_slide: bool = False) -> int:
    raw = LENGTH_FACTOR[level] * source_words(slide_ids, extracts) * scale
    lo, hi = (SLIDE_MIN_WORDS, SLIDE_MAX_WORDS) if per_slide else (MIN_WORDS, MAX_WORDS)
    return int(max(lo, min(hi, round(raw / 10) * 10)))


def section_budget(outline: Outline, idx: int, level: str, extracts: dict[str, SlideExtract]) -> int:
    sec = outline.sections[idx]
    return target_words(level, sec.slides, extracts, sec.budget_scale, outline.mode == "slides")


def system_prompt(level: str, mode: str = "sections") -> str:
    parts = [(PROMPTS_DIR / "common.md").read_text(), (PROMPTS_DIR / f"{level}.md").read_text()]
    if mode == "slides":
        parts.append((PROMPTS_DIR / "per_slide.md").read_text())
    return "\n\n".join(parts)


def cache_dir(chapter: Chapter, outline: Outline, level: str):
    return chapter.dir / MODE_DIRS[outline.mode] / level


def section_input(outline: Outline, idx: int, extracts: dict[str, SlideExtract],
                  slides_by_id: dict[str, Slide], level: str) -> str:
    sec = outline.sections[idx]
    window = range(len(outline.sections))
    if outline.mode == "slides":  # 150-slide decks: show only the neighbourhood
        window = range(max(0, idx - 3), min(len(outline.sections), idx + 4))
    toc = "\n".join(f"{'->' if i == idx else '  '} {i + 1}. {outline.sections[i].title}" for i in window)
    slide_blocks = []
    for sid in sec.slides:
        ex = extracts[sid]
        block = f"### Slide {sid}\n{ex.model_dump_json(exclude={'slide_role': True, 'figures': True})}"
        figs = figure_assets(slides_by_id[sid], ex)
        if figs:
            block += "\nFigures you can embed (use these exact paths):\n" + "\n".join(
                f"- {asset} [{fig.importance} {fig.kind}]: {fig.description}" for asset, fig in figs)
        slide_blocks.append(block)
    known = earlier_terms(outline, idx, extracts)
    return (
        f"Chapter: {outline.chapter_title}\n\nChapter outline (you are writing the section marked ->):\n{toc}\n\n"
        f"Section to write: {sec.title}\nWhat it covers: {sec.summary}\n"
        + ("This section combines several topics: replace the working title with one short, descriptive "
           "`##` heading that covers them.\n" if " & " in sec.title else "")
        + (f"Merge notes: {sec.notes}\n" if sec.notes else "")
        + (f"Already introduced in earlier sections: {', '.join(known)}\n" if known else "")
        + (f"Heading to use: ## {sec.title}\n" if outline.mode == "slides" else "")
        + (part_note(sec) if outline.mode in ("decks", "chapters") else "")
        + (f"{sec.context}\n" if sec.context else "")
        + f"Target length: at most {section_budget(outline, idx, level, extracts)} words. Spend them "
          "on explaining the source content, not on outside background.\n"
        + "\nSource slides:\n\n" + "\n\n".join(slide_blocks)
    )


def part_note(sec: Section) -> str:
    """Grouped modes: a chapter written in several parts must read as one continuous text."""
    if sec.part_count <= 1:
        return f"This section is the whole of '{sec.group}'.\n"
    closing = ("This is the LAST part: end it with the closing block your level asks for "
               "(quiz / takeaways / remember), covering the whole chapter."
               if sec.part_index == sec.part_count else
               "Do NOT end with a quiz, takeaways, summary or 'remember' block: the chapter continues "
               "in the next part.")
    return (f"This section is part {sec.part_index} of {sec.part_count} of '{sec.group}'; the parts are read "
            f"one after another as a single continuous chapter. Give it a short descriptive `##` heading "
            f"for this part, don't re-introduce the chapter, and don't recap earlier parts. {closing}\n")


def earlier_terms(outline: Outline, idx: int, extracts: dict[str, SlideExtract], limit: int = 40) -> list[str]:
    """Key terms from the slides of all previous sections (deduplicated, in order)."""
    seen: dict[str, str] = {}
    for sec in outline.sections[:idx]:
        for sid in sec.slides:
            for term in extracts[sid].key_terms if sid in extracts else []:
                seen.setdefault(term.lower(), term)
    return list(seen.values())[:limit]


def write_section(outline: Outline, idx: int, level: str, extracts: dict[str, SlideExtract],
                  slides_by_id: dict[str, Slide], llm: LLM, chapter: Chapter) -> str:
    sec = outline.sections[idx]
    if sec.kind == "recap":  # repeat of an earlier deck's topic: link + the slides' own words, no rewrite
        return recap_section(sec, extracts)
    if sec.kind != "content":  # title/agenda/"Questions?" slides and exact repeats: no model call
        return f"## {sec.title}\n\n*{sec.context}*"
    sys = system_prompt(level, outline.mode)
    user = section_input(outline, idx, extracts, slides_by_id, level)
    key = hashlib.sha256(f"{sys}\n{user}\n{llm.generation_id}".encode()).hexdigest()[:16]
    path = cache_dir(chapter, outline, level) / f"{idx:02d}-{key}.md"
    if path.exists():
        return path.read_text()

    content: str | list[dict] = user
    if llm.cfg.get("vision"):
        # Let a multimodal writer look at the figures it is explaining (not just their descriptions).
        by_file_id = {slide_file_id(s.id): s for s in slides_by_id.values()}
        parts: list[dict] = [{"type": "text", "text": user}]
        unique: dict[str, tuple] = {}  # a diagram repeated across the section's slides is sent once
        for sid in outline.sections[idx].slides:
            for asset, fig in figure_assets(slides_by_id[sid], extracts[sid]):
                unique.setdefault(asset, (asset, fig))
        figs = sorted(unique.values(), key=lambda af: af[1].importance != "essential")
        for asset, _ in figs[:MAX_WRITER_IMAGES]:
            src = resolve_asset(asset.removeprefix("assets/").removesuffix(".png"), by_file_id)
            if src and src.exists():
                parts += [{"type": "text", "text": f"Image {asset}:"}, image_part(src)]
        content = parts
    messages = [{"role": "system", "content": sys}, {"role": "user", "content": content}]
    # ~1.3 tokens/word plus headroom for markup: a runaway generation is cut off early
    max_tokens = min(8192, int(section_budget(outline, idx, level, extracts) * 2.2) + 600)
    md = llm.chat(messages, max_tokens=max_tokens, temperature=0.4)
    if len(md.strip()) < 80:
        raise ValueError(f"model returned an empty/truncated section ({len(md.strip())} chars)")
    if problem := degeneration(md):
        # regenerate from scratch (never feed the garbage back), cooler and with an explicit warning
        print(f"\n  ! section {idx + 1}: draft rejected ({problem}); regenerating")
        retry = [{"role": "system", "content": sys + "\n\nIMPORTANT: a previous attempt at this section "
                  f"was rejected because it {problem}. Write cleanly and concisely."},
                 {"role": "user", "content": content}]
        md2 = llm.chat(retry, max_tokens=max_tokens, temperature=0.2)
        if len(md2.strip()) >= 80:
            md = md2
        if problem := degeneration(md):
            print(f"\n  ! section {idx + 1}: still {problem}; dropping the affected paragraphs")
            md = strip_degenerate(md)
    if missed := missing_slides(md, outline.sections[idx].slides, extracts):
        # the writer skipped whole slides (e.g. the second subject of a merged chapter): one retry
        listing = "; ".join(f"{sid} '{extracts[sid].title}'" for sid in missed)
        print(f"\n  ! section {idx + 1}: draft skipped slide(s) {listing}; regenerating")
        retry = [{"role": "system", "content": sys + "\n\nIMPORTANT: a previous attempt left out these source "
                  f"slides entirely: {listing}. Cover EVERY source slide, including these."},
                 {"role": "user", "content": content}]
        md2 = llm.chat(retry, max_tokens=max_tokens, temperature=0.3)
        if (len(md2.strip()) >= 80 and not degeneration(md2)
                and len(missing_slides(md2, outline.sections[idx].slides, extracts)) < len(missed)):
            md = md2
    budget = section_budget(outline, idx, level, extracts)
    if len(md.split()) > OVERSHOOT_LIMIT * budget:
        md = tighten(md, budget, llm, max_tokens)
    problems = check_markdown(md)
    if problems:  # one repair round
        messages += [{"role": "assistant", "content": md},
                     {"role": "user", "content": "Rendering problems found: " + "; ".join(problems)
                      + ". Return the full corrected section only."}]
        md = llm.chat(messages, max_tokens=8192, temperature=0.2)
        if still := check_markdown(md):
            print(f"  ! section {idx + 1} '{outline.sections[idx].title}': {'; '.join(still)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md)
    return md


def review_section(md: str, outline: Outline, idx: int, level: str, extracts: dict[str, SlideExtract],
                   llm: LLM, chapter: Chapter) -> str:
    """Fact-check a drafted section against its slides: fix contradictions, move unsupported
    claims into a 'Beyond the slides' box or drop them. Falls back to the draft if the edit
    looks destructive (much shorter, lost its heading, new rendering problems)."""
    sec = outline.sections[idx]
    if sec.kind != "content":
        return md
    sys = (PROMPTS_DIR / "review.md").read_text()
    source = "\n\n".join(f"### Slide {sid}\n{extracts[sid].model_dump_json(exclude={'slide_role'})}"
                          for sid in sec.slides)
    if sec.overlaps:  # reminders that link back to earlier slides are supported by those slides
        source += "\n\nEarlier slides this section may briefly refer back to:\n\n" + "\n\n".join(
            f"### Slide {sid}\n{extracts[sid].model_dump_json(include={'title', 'bullets', 'equations'})}"
            for sid in sec.overlaps if sid in extracts)
    user = f"SOURCE:\n{source}\n\nDRAFT:\n{md}"
    key = hashlib.sha256(f"{sys}\n{user}\n{llm.generation_id}".encode()).hexdigest()[:16]
    path = cache_dir(chapter, outline, level) / f"{idx:02d}-review-{key}.md"
    if path.exists():
        return path.read_text()
    out = llm.chat([{"role": "system", "content": sys}, {"role": "user", "content": user}],
                   max_tokens=min(8192, int(len(md) / 3) + 800), temperature=0.1)
    ok = (out.lstrip().startswith("#") and 0.5 * len(md) <= len(out) <= 1.4 * len(md)
          and len(check_markdown(out)) <= len(check_markdown(md)) and not degeneration(out))
    if not ok:
        why = degeneration(out) or f"len {len(out)} vs {len(md)}"
        print(f"\n  ! section {idx + 1}: fact-check output rejected ({why}); keeping draft")
        return md
    path.write_text(out)
    return out


def write_all(outline: Outline, level: str, extracts: dict[str, SlideExtract], slides: list[Slide],
              llm: LLM, chapter: Chapter, progress: Callable[[int, int], None] | None = None,
              review: bool = True) -> list[str]:
    slides_by_id = {s.id: s for s in slides}
    n = len(outline.sections)
    done = 0
    results: list[str] = [""] * n

    lock = threading.Lock()

    def run(i: int) -> None:
        nonlocal done
        for attempt in (1, 2):
            try:
                results[i] = write_section(outline, i, level, extracts, slides_by_id, llm, chapter)
                if review:
                    try:
                        results[i] = review_section(results[i], outline, i, level, extracts, llm, chapter)
                    except BudgetExceeded:  # a spend cap stops the run; never degrade around it
                        raise
                    except Exception as e:  # a failed check never costs us the draft
                        print(f"\n  ! section {i + 1}: fact-check failed ({type(e).__name__}); keeping draft")
                break
            except BudgetExceeded:  # a spend cap stops the run; never degrade around it
                raise
            except Exception as e:  # keep the rest of the document even if one section fails
                if attempt == 2:
                    print(f"\n  ! section {i + 1} '{outline.sections[i].title}' failed twice "
                          f"({type(e).__name__}: {str(e)[:120]}); using the slide content as-is")
                    results[i] = fallback_section(outline.sections[i], extracts, slides_by_id)
        with lock:
            done += 1
            if progress:
                progress(done, n)

    with ThreadPoolExecutor(max_workers=llm.max_concurrency) as pool:
        list(pool.map(run, range(n)))
    return results


TIGHTEN_PROMPT = """Shorten the study-notes section below to at most {budget} words.
Keep: every heading, fact, definition, formula, code block, table, image line and quiz question. Keep the tone and level.
Cut: repetition, wordy phrasing, filler sentences, restating the same idea twice. Do not add anything.
Return only the shortened Markdown section."""


def tighten(md: str, budget: int, llm: LLM, max_tokens: int) -> str:
    """One compression pass for sections that ignored their word budget. Keeps the original if the
    result isn't clearly better (not shorter, gutted, or degenerate)."""
    out = llm.chat([{"role": "system", "content": TIGHTEN_PROMPT.format(budget=budget)},
                    {"role": "user", "content": md}], max_tokens=max_tokens, temperature=0.2)
    before, after = len(md.split()), len(out.split())
    images_kept = all(img in out for img in IMAGE_RE.findall(md))
    if out.lstrip().startswith("#") and 0.3 * before <= after < before and images_kept and not degeneration(out):
        return out
    return md


def recap_section(sec: Section, extracts: dict[str, SlideExtract]) -> str:
    """Short, lossless recap: where it was explained, then the slides' points verbatim."""
    blocks = [f"## {sec.title}", f"*{sec.context} The slides recap these points:*"]
    for sid in sec.slides:
        ex = extracts[sid]
        bullets = [f"- {b.strip().removeprefix('- ')}" for b in ex.bullets if b.strip()]
        if bullets:
            blocks.append("\n".join(bullets))
        blocks += [f"$$\n{eq}\n$$" for eq in ex.equations]
        blocks += [f"```{c.language}\n{c.code}\n```" for c in ex.code]
        blocks += ex.tables
    return "\n\n".join(blocks)


def fallback_section(sec: Section, extracts: dict[str, SlideExtract], slides_by_id: dict[str, Slide]) -> str:
    """Faithful plain rendering of a section's slide content (no model) — never cached."""
    out = [f"## {sec.title}", "> **Note:** this section could not be rewritten automatically; "
                              "it shows the original slide content."]
    for sid in sec.slides:
        ex = extracts[sid]
        out.append(f"### {ex.title}")
        out += [f"{'  ' if b.startswith('  ') else ''}- {b.strip().removeprefix('- ')}" for b in ex.bullets]
        out += [f"$$\n{eq}\n$$" for eq in ex.equations]
        out += [f"```{c.language}\n{c.code}\n```" for c in ex.code]
        out += ex.tables
        out += [f"![{f.kind}]({asset})\n\n*{f.description}*" for asset, f in figure_assets(slides_by_id[sid], ex)]
    return "\n\n".join(out)
