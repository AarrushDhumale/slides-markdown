"""Per-slide mode: one section per slide, in deck order, with overlap between slides handled.

Overlap is resolved *before* writing, deterministically:
- a slide identical to an earlier one becomes a one-line pointer to it (no model call);
- title / agenda / "Questions?" slides become a one-line note (no model call);
- for every other slide, each bullet, equation, code block and figure that already appeared on an
  earlier slide is listed for the writer with a link to that slide's section, so it explains only
  what is new and gives a one-line reminder for the rest. The word budget shrinks accordingly.
"""

from __future__ import annotations

import difflib
import re

from backend.pipeline.assemble import anchor
from backend.pipeline.chapter import SEP, Slide
from backend.pipeline.extract import SlideExtract, figure_assets
from backend.pipeline.outline import FILLER_ROLES, Outline, Section

SAME_BULLET_RATIO = 0.85
MIN_BULLET_WORDS = 3


def slide_label(sid: str, multi_pdf: bool) -> str:
    """`A·12` -> 'Slide 12' (single PDF) or 'Slide A12' (chapter of several PDFs)."""
    letter, page = sid.split(SEP)
    return f"Slide {letter}{page}" if multi_pdf else f"Slide {page}"


def _norm(text: str) -> str:
    text = re.sub(r"^[\s\-*•●○◦▪]+", "", text.lower())
    return re.sub(r"[^a-z0-9$^_{}\\]+", " ", text).strip()


def _same(item: str, earlier_norm: str) -> bool:
    a = _norm(item)
    if len(a.split()) < MIN_BULLET_WORDS and not any(c in item for c in "$=\\"):
        return False  # too short to call it repeated content
    return difflib.SequenceMatcher(None, a, earlier_norm, autojunk=False).ratio() >= SAME_BULLET_RATIO


def build_per_slide_outline(chapter_title: str, overview: str, slides: list[Slide],
                            extracts: dict[str, SlideExtract]) -> Outline:
    multi_pdf = len({s.pdf.sha for s in slides}) > 1
    order = list(slides)
    label = {s.id: slide_label(s.id, multi_pdf) for s in order}
    titles: dict[str, str] = {}
    for s in order:
        ex = extracts.get(s.id) or extracts.get(s.duplicate_of or "")
        titles[s.id] = f"{label[s.id]} · {ex.title.strip() if ex and ex.title.strip() else 'Untitled'}"
    link = {sid: f"[{label[sid]}](#{anchor(t)})" for sid, t in titles.items()}

    # what each earlier slide contributed, normalised, for overlap matching
    seen_items: list[tuple[str, str]] = []      # (normalised item, slide id)
    seen_assets: dict[str, str] = {}             # asset path -> first slide id
    sections: list[Section] = []
    for s in order:
        sid = s.id
        if s.duplicate_of and sid not in extracts:
            sections.append(Section(title=titles[sid], slides=[sid], kind="duplicate", overlaps=[s.duplicate_of],
                                    context=f"This slide repeats {link[s.duplicate_of]}; see the explanation there."))
            continue
        ex = extracts[sid]
        if ex.slide_role in FILLER_ROLES:
            what = {"title": "Title slide", "agenda": "Agenda", "references": "References",
                    "filler": "Transition slide"}[ex.slide_role]
            body = "; ".join(b.strip("- ") for b in ex.bullets[:3])
            sections.append(Section(title=titles[sid], slides=[sid], kind="filler",
                                    context=f"{what}{': ' + body if body else ''}. No new teaching content."))
            continue

        repeated: list[str] = []
        overlap_ids: list[str] = []
        new_words = repeated_words = 0
        items = [("point", b) for b in ex.bullets] + [("formula", e) for e in ex.equations] + \
                [("code", c.code) for c in ex.code] + [("table", t) for t in ex.tables]
        for kind, item in items:
            n = len(item.split())
            owner = next((osid for norm_item, osid in seen_items if _same(item, norm_item)), None)
            if owner:
                repeated.append(f"- {kind} \"{item.strip()[:160]}\" → already explained in {link[owner]}")
                repeated_words += n
                overlap_ids.append(owner)
            else:
                new_words += n
        for asset, _fig in figure_assets(s, ex):
            if asset in seen_assets and seen_assets[asset] != sid:
                repeated.append(f"- figure `{asset}` → already shown in {link[seen_assets[asset]]} "
                                "(refer to it by link instead of embedding it again)")
                overlap_ids.append(seen_assets[asset])
            else:
                seen_assets.setdefault(asset, sid)
        context = ""
        if repeated:
            context = ("Overlap with earlier slides (give each a one-sentence reminder with its link, "
                       "do not re-explain):\n" + "\n".join(repeated))
        total = new_words + repeated_words
        scale = 1.0 if not total else max(0.35, (new_words + 0.2 * repeated_words) / total)
        sections.append(Section(title=titles[sid], slides=[sid], context=context,
                                overlaps=list(dict.fromkeys(overlap_ids)), budget_scale=round(scale, 2)))
        seen_items += [(_norm(item), sid) for _, item in items]
    return Outline(chapter_title=chapter_title, overview=overview, sections=sections, mode="slides")
