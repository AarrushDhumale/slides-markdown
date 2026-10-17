"""Per-deck mode: one part per slide deck (PDF), in the order given, each split into topic sections.

Each deck gets its own topic outline (topics are never merged across decks). Overlap *between*
decks — a later lecture recapping an earlier one — is resolved before writing: items (bullets,
formulas, code, tables, figures) already present in an earlier deck are listed for the writer with
a link to that deck's part, so it gives a one-line reminder instead of re-explaining, and the
section's word budget shrinks. Slides that are exact repeats of an earlier deck's slides are noted
in the deck's intro.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from backend.llm.client import LLM
from backend.llm.usage import BudgetExceeded
from backend.paths import PROMPTS_DIR
from backend.pipeline.assemble import anchor
from backend.pipeline.chapter import SEP, Chapter, Slide, pdf_letter
from backend.pipeline.extract import SlideExtract, figure_assets
from backend.pipeline.ingest import IngestedPdf
from backend.pipeline.outline import Outline, build_outline
from backend.pipeline.per_slide import _norm, _same


class SectionOverlap(BaseModel):
    section: int                      # 1-based index within the current deck
    earlier: list[int] = []           # 1-based indices into the earlier-sections list
    relation: Literal["same", "builds_on", "none"] = "none"
    new_in_this_section: str = ""


class DeckOverlap(BaseModel):
    overlaps: list[SectionOverlap] = []


def _section_digest(sec, extracts: dict[str, SlideExtract], bullets: int = 6) -> str:
    pts = [b.strip()[:120] for sid in sec.slides for b in extracts[sid].bullets][:bullets]
    terms = sorted({t for sid in sec.slides for t in extracts[sid].key_terms})[:12]
    return (f"{sec.title}" + (f" — {sec.summary}" if sec.summary else "")
            + ("".join(f"\n    · {p}" for p in pts)) + (f"\n    key terms: {', '.join(terms)}" if terms else ""))


def topic_overlaps(chapter: Chapter, letter: str, earlier: list, current: list,
                   extracts: dict[str, SlideExtract], llm: LLM) -> DeckOverlap:
    """Semantic overlap between this deck's sections and earlier decks' sections (one cached call)."""
    prompt = (PROMPTS_DIR / "deck_overlap.md").read_text()
    user = ("EARLIER sections:\n" + "\n".join(f"E{i}. [{g}] {_section_digest(s, extracts)}"
                                               for i, (g, s) in enumerate(earlier, 1))
            + "\n\nCURRENT deck sections:\n" + "\n".join(f"S{i}. {_section_digest(s, extracts)}"
                                                         for i, s in enumerate(current, 1))
            + "\n\nRefer to sections by number only (S1 -> section 1, E3 -> earlier 3).")
    key = hashlib.sha256(f"{prompt}\n{user}\n{llm.model_id}".encode()).hexdigest()[:16]
    path = chapter.dir / f"overlap-{letter}.json"
    if path.exists() and json.loads(path.read_text()).get("key") == key:
        return DeckOverlap.model_validate(json.loads(path.read_text())["result"])
    try:
        llm.connect()
        result = llm.structured([{"role": "system", "content": prompt}, {"role": "user", "content": user}],
                                DeckOverlap, max_tokens=2048)
    except BudgetExceeded:  # a spend cap stops the run; never degrade around it
        raise
    except Exception as e:  # overlap hints are an optimisation; never fail the run over them
        print(f"\n  ! topic-overlap check for deck {letter} failed ({type(e).__name__}); skipping it")
        return DeckOverlap()
    path.write_text(json.dumps({"key": key, "result": result.model_dump()}, indent=1, ensure_ascii=False))
    return result


def slide_items(ex: SlideExtract) -> list[tuple[str, str]]:
    return ([("point", b) for b in ex.bullets] + [("formula", e) for e in ex.equations]
            + [("code", c.code) for c in ex.code] + [("table", t) for t in ex.tables])


GROUP_SETTINGS = {
    # mode: (heading prefix, slides per part, max slides per part, min parts)
    "decks": ("Deck", 4, 8, 2),
    "chapters": ("Chapter", 10, 12, 1),   # few, substantial parts; read as one continuous chapter
}


def _slide_range(group: list[Slide]) -> str:
    first, last = group[0].id, group[-1].id
    return first if first == last else f"{first}–{last}"


def build_per_deck_outline(chapter: Chapter, pdfs: list[IngestedPdf], slides: list[Slide],
                           extracts: dict[str, SlideExtract], llm: LLM, extract_model: str) -> Outline:
    groups = []
    for i, pdf in enumerate(pdfs):
        letter = pdf_letter(i)
        deck = [s for s in slides if s.id.split(SEP)[0] == letter]
        groups.append((letter, None, deck, f"*Source: {Path(pdf.source).name}*"))
    return build_grouped_outline(chapter, groups, extracts, llm, extract_model, "decks")


def build_per_chapter_outline(chapter: Chapter, detected: list[tuple[str, list[Slide]]],
                              extracts: dict[str, SlideExtract], llm: LLM, extract_model: str) -> Outline:
    groups = []
    for i, (title, group) in enumerate(detected):
        files = list(dict.fromkeys(Path(s.pdf.source).name for s in group))
        groups.append((f"ch{i + 1}", title, group, f"*Slides {_slide_range(group)} ({', '.join(files)})*"))
    return build_grouped_outline(chapter, groups, extracts, llm, extract_model, "chapters")


def build_grouped_outline(chapter: Chapter, groups: list[tuple], extracts: dict[str, SlideExtract],
                          llm: LLM, extract_model: str, mode: str) -> Outline:
    """groups: [(key, title or None, slides, source note)]. Each group becomes one `##` part of the
    doc, split into topic parts; overlap with *earlier* groups is resolved before writing."""
    prefix, per_part, max_part, min_parts = GROUP_SETTINGS[mode]
    sections, overview_lines = [], []
    seen_items: list[tuple[str, str, str]] = []   # (normalised item, group heading, slide id)
    earlier_sections: list = []                   # (group heading, Section) of all earlier groups
    seen_assets: dict[str, tuple[str, str]] = {}  # asset -> (group heading, slide id)
    group_of: dict[str, str] = {}                 # slide id -> group heading

    for i, (key, title, group_slides, source_note) in enumerate(groups):
        sub = build_outline(chapter, group_slides, extracts, llm, extract_model, cache_name=f"outline-{mode}-{key}",
                            per_section=per_part, max_slides=max_part, min_sections=min_parts,
                            title_hint=title or "")
        heading = f"{prefix} {i + 1} · {title or sub.chapter_title}"
        link = f"[{heading}](#{anchor(heading)})"
        for s in group_slides:
            group_of[s.id] = heading

        intro = [source_note]
        if sub.overview:
            intro.append(sub.overview)
        repeats = [s for s in group_slides if s.duplicate_of and group_of.get(s.duplicate_of) not in (None, heading)]
        if repeats:
            where = sorted({group_of[s.duplicate_of] for s in repeats})
            intro.append(f"*{len(repeats)} recap slide(s) here repeat material covered in "
                         + ", ".join(f"[{w}](#{anchor(w)})" for w in where) + ".*")
        overview_lines.append(f"- {link}: {sub.overview.split('. ')[0].rstrip('.')}." if sub.overview else f"- {link}")

        topics = {}
        if earlier_sections:
            res = topic_overlaps(chapter, f"{mode}-{key}", earlier_sections, sub.sections, extracts, llm)
            for o in res.overlaps:
                refs = [earlier_sections[e - 1] for e in o.earlier if 1 <= e <= len(earlier_sections)]
                if 1 <= o.section <= len(sub.sections) and o.relation != "none" and refs:
                    topics[o.section - 1] = (o.relation, refs, o.new_in_this_section)

        group_items: list[tuple[str, str, str]] = []
        for k, sec in enumerate(sub.sections):
            sec.group = heading
            sec.group_intro = "\n\n".join(intro) if k == 0 else ""
            sec.part_index, sec.part_count = k + 1, len(sub.sections)
            repeated, overlap_ids = [], []
            new_words = repeated_words = 0
            for sid in sec.slides:
                ex = extracts[sid]
                for kind, item in slide_items(ex):
                    n = len(item.split())
                    hit = next(((h, osid) for norm, h, osid in seen_items if _same(item, norm)), None)
                    if hit:
                        repeated.append(f"- {kind} \"{item.strip()[:160]}\" → already explained in "
                                        f"[{hit[0]}](#{anchor(hit[0])})")
                        overlap_ids.append(hit[1])
                        repeated_words += n
                    else:
                        new_words += n
                    group_items.append((_norm(item), heading, sid))
                slide = next(s for s in group_slides if s.id == sid)
                for asset, _fig in figure_assets(slide, ex):
                    if asset in seen_assets and seen_assets[asset][0] != heading:
                        h, osid = seen_assets[asset]
                        repeated.append(f"- figure `{asset}` → already shown in [{h}](#{anchor(h)}) "
                                        "(link to it instead of embedding it again)")
                        overlap_ids.append(osid)
                    else:
                        seen_assets.setdefault(asset, (heading, sid))
            notes = []
            if k in topics:
                relation, refs, new = topics[k]
                where = "; ".join(f"'{s.title}' in [{g}](#{anchor(g)})" for g, s in refs)
                if relation == "same" and (not new or new.strip().lower().startswith("nothing new")):
                    # pure repeat: no rewrite at all — a link plus the slide's own words (lossless, short)
                    sec.kind = "recap"
                    sec.context = f"This revisits material explained in {where}."
                    sec.overlaps = [sid for _, s in refs for sid in s.slides]
                    sections.append(sec)
                    continue
                if relation == "same":
                    notes.append(f"This topic was already explained in {where}. Do NOT re-explain it: give a 2-3 "
                                 f"sentence recap that links there, then spend the words on what is new here"
                                 f"{': ' + new if new and new.lower() != 'nothing new' else ''}.")
                else:
                    notes.append(f"This section builds on {where}. Refer back to those concepts briefly with the "
                                 "link instead of re-defining them.")
                overlap_ids += [sid for _, s in refs for sid in s.slides]
            if repeated:
                notes.append("Overlap with earlier material (give each a one-sentence reminder with its link, "
                             "do not re-explain):\n" + "\n".join(repeated))
            if notes:
                sec.context = "\n".join(notes)
                total = new_words + repeated_words
                scale = (new_words + 0.2 * repeated_words) / total if total else 1.0
                if k in topics:
                    scale = min(scale, 0.5 if topics[k][0] == "same" else 0.85)
                sec.budget_scale = round(max(0.35, scale), 2)
                sec.overlaps = list(dict.fromkeys(overlap_ids))
            sections.append(sec)
        seen_items += group_items  # only *earlier* groups count as overlap
        earlier_sections += [(heading, sec) for sec in sub.sections]

    what = "lecture deck" if mode == "decks" else "chapter"
    overview = f"These notes are organised by {what}:\n\n" + "\n".join(overview_lines)
    if mode == "chapters" and len(groups) == 1:
        overview = sections[0].group_intro.split("\n\n", 1)[-1] if sections else ""
    return Outline(chapter_title=chapter.name, overview=overview, sections=sections, mode=mode)
