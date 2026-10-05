"""Stage 3: group all slides of a chapter into logical sections (merging across PDFs)."""

from __future__ import annotations

import hashlib
import json
import re

from pydantic import BaseModel, Field, field_validator
from pydantic.json_schema import SkipJsonSchema

from backend.llm.client import LLM
from backend.paths import PROMPTS_DIR
from backend.pipeline.chapter import Chapter, Slide
from backend.pipeline.extract import SlideExtract

FULL_DETAIL_CHAR_BUDGET = 300_000  # ~75k tokens; above this the outline sees compact extracts
FILLER_ROLES = {"title", "agenda", "filler", "references"}
MAX_SLIDES_PER_SECTION = 8  # bigger sections produce long, unfocused (and runaway-prone) generations


def _ids(value):
    """Accept ['A·1'] as well as [{'slide_id': 'A·1', 'reason': ...}] / [{'id': 'A·1'}] / 'A·1, A·2'."""
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    out = []
    for v in value or []:
        if isinstance(v, dict):
            v = v.get("slide_id") or v.get("id") or v.get("slide") or next(iter(v.values()), "")
        out.append(str(v).strip())
    return out


class Section(BaseModel):
    title: str
    slides: list[str] = Field(description="Slide ids in teaching order, e.g. ['A·4', 'A·5', 'B·2']")
    summary: str = Field(default="", description="1-2 sentences: what this section teaches")
    notes: str = Field(default="", description="Merge notes, e.g. 'B·2 repeats the definition from A·5'; empty if none")
    # set by code, never by the model (hidden from the JSON schema):
    kind: SkipJsonSchema[str] = "content"         # content | filler | duplicate (per-slide mode)
    group: SkipJsonSchema[str] = ""               # per-deck mode: heading of the deck this section belongs to
    group_intro: SkipJsonSchema[str] = ""         # per-deck mode: text under that heading (first section only)
    part_index: SkipJsonSchema[int] = 1           # grouped modes: this section is part k of n of its group
    part_count: SkipJsonSchema[int] = 1
    context: SkipJsonSchema[str] = ""             # extra writer guidance, e.g. overlap with earlier slides
    overlaps: SkipJsonSchema[list[str]] = []      # earlier slide ids whose content this section reuses
    budget_scale: SkipJsonSchema[float] = 1.0     # shrink the word budget when content is repeated

    _slides = field_validator("slides", mode="before")(_ids)


MODE_DIRS = {"chapters": "sections-by-chapter", "sections": "sections", "slides": "sections-by-slide",
             "decks": "sections-by-deck"}
# the chapter-wise doc is the main one (`learner.md`); the other layouts are opt-in variants
MODE_SUFFIX = {"chapters": "", "sections": "-by-topic", "slides": "-by-slide", "decks": "-by-deck"}
GROUPED_MODES = ("decks", "chapters")


class Outline(BaseModel):
    chapter_title: str
    overview: str = Field(default="", description="2-3 sentence overview of the whole chapter")
    sections: list[Section]
    mode: SkipJsonSchema[str] = "sections"        # "sections" (topics) | "slides" (one per slide) | "decks"
    filler: list[str] = Field(default_factory=list, description="Slide ids with no teaching content (title, agenda, thanks, ...)")

    _filler = field_validator("filler", mode="before")(_ids)


def render_slide(sid: str, ex: SlideExtract, compact: bool) -> str:
    if not compact:
        return f"### {sid}\n{ex.model_dump_json(exclude_none=True)}"
    parts = [f"### {sid} [{ex.slide_role}] {ex.title}"]
    parts += [f"- {b}" for b in ex.bullets[:3]]
    if ex.equations:
        parts.append(f"(+{len(ex.equations)} equations)")
    if ex.code:
        parts.append(f"(+{len(ex.code)} code blocks: {', '.join(c.language for c in ex.code)})")
    if ex.figures:
        parts.append(f"(+figures: {'; '.join(f.description[:80] for f in ex.figures)})")
    return "\n".join(parts)


def _cache_key(chapter_pdfs: list[str], extract_model: str, llm: LLM, prompt: str) -> str:
    raw = json.dumps([chapter_pdfs, extract_model, llm.model_id, prompt])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def repair(outline: Outline, order: list[str], filler_roles: set[str],
           extracts: dict[str, SlideExtract], target: int | None = None,
           max_slides: int = MAX_SLIDES_PER_SECTION) -> Outline:
    """Ensure every slide appears exactly once (in a section or in filler)."""
    valid = set(order)
    seen: set[str] = set()
    # Slides the extractor classified as non-teaching never become sections, whatever the outline says.
    forced_filler = [s for s in order if extracts[s].slide_role in filler_roles]
    seen.update(forced_filler)
    for sec in outline.sections:
        sec.slides = [s for s in sec.slides if s in valid and not (s in seen or seen.add(s))]
    outline.sections = [s for s in outline.sections if s.slides]
    outline.filler = forced_filler + [s for s in outline.filler if s in valid and not (s in seen or seen.add(s))]

    pos = {sid: i for i, sid in enumerate(order)}
    for sid in order:
        if sid in seen:
            continue
        if not outline.sections:
            outline.filler.append(sid)
            continue
        # attach to the section holding the nearest preceding slide
        best = min(outline.sections,
                   key=lambda sec: min((pos[sid] - pos[s] for s in sec.slides if pos[s] < pos[sid]),
                                       default=10**9))
        best.slides.append(sid)
        best.slides.sort(key=pos.__getitem__)
    for sec in outline.sections:
        sec.title = re.sub(r"^\s*(section\s+)?\d+[.):]\s*", "", sec.title, flags=re.I).strip() or sec.title
    outline = merge_continuations(outline, order, extracts)
    if target:
        outline = cap_sections(outline, order, max_sections=max(target + 2, round(1.5 * target)),
                               max_slides=max_slides)
    return split_large_sections(outline, max_slides)


def split_large_sections(outline: Outline, max_slides: int = MAX_SLIDES_PER_SECTION) -> Outline:
    """Split oversized sections into evenly sized consecutive parts."""
    out: list[Section] = []
    for sec in outline.sections:
        n = len(sec.slides)
        parts = -(-n // max_slides)  # ceil
        if parts <= 1:
            out.append(sec)
            continue
        size = -(-n // parts)
        for k in range(parts):
            chunk = sec.slides[k * size:(k + 1) * size]
            out.append(Section(title=f"{sec.title} (part {k + 1})", slides=chunk,
                               summary=sec.summary if k == 0 else "", notes=sec.notes if k == 0 else ""))
    outline.sections = out
    return outline


def target_sections(n_content_slides: int, per_section: int = 4, minimum: int = 2) -> int:
    """About one section per `per_section` teaching slides (minimum..12)."""
    return max(minimum, min(12, round(n_content_slides / per_section)))


def cap_sections(outline: Outline, order: list[str], max_sections: int,
                 max_slides: int = MAX_SLIDES_PER_SECTION) -> Outline:
    """If the model fragmented the chapter (e.g. one section per slide), merge the smallest
    section into its smaller neighbour until the count is reasonable. Order is preserved."""
    secs = outline.sections
    while len(secs) > max_sections:
        # merge the adjacent pair with the fewest combined slides, never beyond the section size cap
        pairs = [(len(secs[k].slides) + len(secs[k + 1].slides), k) for k in range(len(secs) - 1)]
        pairs = [pk for pk in pairs if pk[0] <= max_slides]
        if not pairs:
            break
        a = min(pairs)[1]
        b = a + 1
        merged = Section(
            title=f"{secs[a].title} & {secs[b].title}",
            slides=secs[a].slides + secs[b].slides,
            summary=" ".join(x for x in (secs[a].summary, secs[b].summary) if x),
            notes=" ".join(x for x in (secs[a].notes, secs[b].notes) if x),
        )
        secs = secs[:a] + [merged] + secs[b + 1:]
    outline.sections = secs
    return outline


CONT_RE = re.compile(r"\((cont(inued)?\.?|contd\.?)\)|\bcont(inued|d)?\.?\s*$", re.I)


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", CONT_RE.sub("", t.lower())).strip()


def merge_continuations(outline: Outline, order: list[str], extracts: dict[str, SlideExtract]) -> Outline:
    """A section starting with a '(cont.)' slide, or with a slide titled the same as the slide just
    before it (lecture 'builds' and multi-slide topics), belongs with that previous slide's section."""
    pos = {sid: i for i, sid in enumerate(order)}
    merged: list[Section] = []
    for sec in outline.sections:
        first = min(sec.slides, key=pos.__getitem__)
        prev_idx = pos[first] - 1
        target = next((m for m in merged if prev_idx >= 0 and order[prev_idx] in m.slides), None)
        same_title = prev_idx >= 0 and _norm_title(extracts[first].title) == _norm_title(extracts[order[prev_idx]].title)
        if target and (CONT_RE.search(extracts[first].title) or same_title):
            target.slides = sorted(target.slides + sec.slides, key=pos.__getitem__)
            target.notes = (target.notes + f" Merged continuation '{sec.title}'.").strip()
        else:
            merged.append(sec)
    outline.sections = merged
    return outline


def build_outline(chapter: Chapter, slides: list[Slide], extracts: dict[str, SlideExtract],
                  llm: LLM, extract_model: str, cache_name: str = "outline", per_section: int = 4,
                  max_slides: int = MAX_SLIDES_PER_SECTION, min_sections: int = 2,
                  title_hint: str = "") -> Outline:
    prompt = (PROMPTS_DIR / "outline.md").read_text()
    order = [s.id for s in slides if s.id in extracts]
    content = [sid for sid in order if extracts[sid].slide_role not in FILLER_ROLES]
    target = target_sections(len(content), per_section, min_sections)
    if target == 1 and len(content) <= max_slides and title_hint:
        # small enough to write as one piece: no outline call, no split
        one = Outline(chapter_title=title_hint, sections=[Section(title=title_hint, slides=content or order)],
                      filler=[sid for sid in order if sid not in content])
        return one
    key = _cache_key([s.pdf.sha for s in slides if s.page == 1] + order[:1] + order[-1:], extract_model, llm,
                     f"{prompt}\n{target}\n{max_slides}")
    path = chapter.dir / f"{cache_name}.json"
    if path.exists():
        cached = json.loads(path.read_text())
        if cached.get("key") == key:
            return repair(Outline.model_validate(cached["outline"]), order, FILLER_ROLES, extracts, target,
                          max_slides)

    full = "\n\n".join(render_slide(sid, extracts[sid], compact=False) for sid in order)
    compact = len(full) > FULL_DETAIL_CHAR_BUDGET
    body = full if not compact else "\n\n".join(render_slide(sid, extracts[sid], True) for sid in order)
    pdf_note = ", ".join(f"{s.id.split('·')[0]} = {s.pdf.title}" for s in slides if s.page == 1)
    dups = [f"{s.id} duplicates {s.duplicate_of}" for s in slides if s.duplicate_of]

    user = (f"Chapter name: {chapter.name}\nSource PDFs in order: {pdf_note}\n"
            + (f"Already removed as exact duplicates: {', '.join(dups)}\n" if dups else "")
            + f"Aim for about {target} sections (a typical section covers about {per_section} slides; prefer "
              f"fewer, substantial sections over many small ones).\n"
            + f"\nSlides:\n\n{body}")
    llm.connect()
    outline = llm.structured(
        [{"role": "system", "content": prompt}, {"role": "user", "content": user}],
        Outline, max_tokens=8192,
    )
    outline = repair(outline, order, FILLER_ROLES, extracts, target, max_slides)
    path.write_text(json.dumps({"key": key, "outline": outline.model_dump()}, indent=1, ensure_ascii=False))
    return outline
