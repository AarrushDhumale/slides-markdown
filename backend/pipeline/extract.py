"""Stage 2: per-slide VLM extraction -> SlideExtract JSON, cached per (PDF, page, model).

The VLM sees the full slide plus each cropped figure candidate (labelled f1, f2, ...),
so it can describe every figure and say which crop shows it and how important it is.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Literal

from pydantic import BaseModel, Field

from backend.llm.client import LLM, image_part
from backend.paths import PROMPTS_DIR
from backend.pipeline.chapter import Slide

EXTRACT_VERSION = "v3"  # bump when the schema/prompt changes to invalidate cached extracts


class CodeBlock(BaseModel):
    language: str = Field(description="e.g. python, c, java, pseudocode")
    code: str


class Figure(BaseModel):
    kind: Literal["diagram", "chart", "photo", "table_image", "screenshot", "equation_image", "other"]
    description: str = Field(description="What the figure shows and what it teaches: components, labels, "
                                         "arrows/flow, axes and trends. 2-6 sentences.")
    importance: Literal["essential", "helpful", "decorative"] = Field(
        default="helpful",
        description="essential: the concept can't be understood without it; helpful: illustrates the text; "
                    "decorative: logos, stock photos, ornaments")
    crop: str | None = Field(default=None, description="Id of the cropped region showing this figure "
                                                       "(e.g. 'f2'), or null if no crop matches")


class SlideExtract(BaseModel):
    slide_role: Literal["title", "agenda", "content", "example", "exercise", "summary", "references", "filler"]
    title: str
    # list fields default to [] — some backends (Ollama + newer archs) omit empty fields despite the schema
    bullets: list[str] = Field(default_factory=list, description="Text content in reading order; nested items prefixed with '  - '")
    equations: list[str] = Field(default_factory=list, description="Each equation as LaTeX without $ delimiters")
    code: list[CodeBlock] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list, description="Each table as a GitHub-flavored Markdown table")
    figures: list[Figure] = Field(default_factory=list)
    key_terms: list[str] = Field(default_factory=list, description="Technical terms introduced or used centrally on this slide")


def figure_assets(slide: Slide, ex: SlideExtract) -> list[tuple[str, Figure]]:
    """Non-decorative figures as (asset path used in the docs, figure).

    A figure with a matching crop uses the crop (`assets/A-003-f1.png`) — or the first identical
    crop elsewhere in the chapter, so repeated diagrams share one file; otherwise the whole
    slide image (`assets/A-003.png`) stands in for it.
    """
    out, seen = [], set()
    for fig in ex.figures:
        if fig.importance == "decorative":
            continue
        asset = f"assets/{slide.figure_asset(fig.crop)}.png"
        if asset not in seen:
            seen.add(asset)
            out.append((asset, fig))
    return out


def model_slug(model: str) -> str:
    return re.sub(r"[^\w.-]+", "_", model)


def _cache_path(slide: Slide, llm: LLM):
    return slide.pdf.dir / "extract" / model_slug(llm.model_id) / EXTRACT_VERSION / f"{slide.page:03d}.json"


def _messages(slide: Slide, system_prompt: str) -> list[dict]:
    text_hint = slide.text or "(no text layer — read everything from the image)"
    content: list[dict] = [
        {"type": "text", "text": "Full slide:"},
        image_part(slide.image_path),
    ]
    for crop in slide.figures:
        content += [{"type": "text", "text": f"Cropped region {crop.id} ({crop.source} graphic):"},
                    image_part(slide.pdf.dir / crop.path)]
    crops_note = (f"Cropped regions available: {', '.join(c.id for c in slide.figures)}."
                  if slide.figures else "No cropped regions were detected; set `crop` to null.")
    content.append({"type": "text", "text": (
        f"{crops_note}\n\nPDF text layer for this slide (may be garbled or incomplete; "
        f"the image is authoritative):\n<<<\n{text_hint}\n>>>")})
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": content}]


def extract_slide(slide: Slide, llm: LLM, system_prompt: str) -> SlideExtract:
    path = _cache_path(slide, llm)
    if path.exists():
        return SlideExtract.model_validate_json(path.read_text())
    result = sanitize(llm.structured(_messages(slide, system_prompt), SlideExtract, max_tokens=6144), slide)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(indent=1))
    return result


META_BULLET_RE = re.compile(r"^(-\s*)?(this|the) (slide|diagram|figure|image) (shows|illustrates|depicts|"
                            r"presents|explains|describes)\b", re.I)


def sanitize(ex: SlideExtract, slide: Slide) -> SlideExtract:
    """Normalise model quirks: empty entries, $-wrapped LaTeX, narrated bullets, bogus crop ids."""
    ex.bullets = [b.rstrip() for b in ex.bullets if b.strip() and not META_BULLET_RE.match(b.strip())]
    ex.equations = [e.strip().strip("$").strip() for e in ex.equations if e.strip().strip("$").strip()]
    ex.equations = [re.sub(r"^\\\[|\\\]$", "", e).strip() for e in ex.equations]
    # "P$ = Present bit"-style entries are definitions, not math: move them to bullets
    defs = [e for e in ex.equations if "$" in e or re.search(r"[A-Za-z]{4,} [a-z]{3,} [a-z]{3,}", e)]
    ex.equations = [e for e in ex.equations if e not in defs]
    ex.bullets += [f"- {e.replace('$', '')}" for e in defs]
    ex.tables = [t.strip() for t in ex.tables if t.strip().count("|") >= 2]
    ex.code = [c for c in ex.code if c.code.strip()]
    ex.key_terms = [k.strip() for k in ex.key_terms if k.strip()]
    valid = {c.id for c in slide.figures}
    for fig in ex.figures:
        if fig.crop not in valid:
            fig.crop = None
    ex.figures = [f for f in ex.figures if f.description.strip()]
    return ex


def extract_all(slides: list[Slide], llm: LLM,
                progress: Callable[[int, int], None] | None = None) -> dict[str, SlideExtract]:
    """Extract every non-duplicate slide concurrently. Returns {slide_id: extract}."""
    system_prompt = (PROMPTS_DIR / "extract.md").read_text()
    todo = [s for s in slides if not s.duplicate_of]
    if any(not _cache_path(s, llm).exists() for s in todo):
        llm.connect()
    results: dict[str, SlideExtract] = {}
    failures: list[tuple[Slide, Exception]] = []
    with ThreadPoolExecutor(max_workers=llm.max_concurrency) as pool:
        futures = {pool.submit(extract_slide, s, llm, system_prompt): s for s in todo}
        for n, fut in enumerate(as_completed(futures), start=1):
            slide = futures[fut]
            try:
                results[slide.id] = fut.result()
            except Exception as e:  # one bad slide (malformed JSON, timeout) must not sink the deck
                failures.append((slide, e))
            if progress:
                progress(n, len(todo))
    if failures and len(failures) > MAX_FAILED_FRACTION * len(todo):
        raise RuntimeError(f"slide extraction failed for {len(failures)}/{len(todo)} slides; "
                           f"first error: {failures[0][1]}") from failures[0][1]
    for slide, e in failures:
        print(f"\n  ! {slide.id}: extraction failed ({type(e).__name__}: {str(e)[:120]}); "
              "using the PDF text layer instead (not cached, will retry next run)")
        results[slide.id] = fallback_extract(slide)
    return results


MAX_FAILED_FRACTION = 0.3


def fallback_extract(slide: Slide) -> SlideExtract:
    """Best effort without a model: the PDF text layer plus the detected figure crops."""
    lines = [ln.strip("●○•◦▪- \t") for ln in slide.text.splitlines()]
    lines = [ln for ln in lines if ln]
    return SlideExtract(
        slide_role="content" if lines or slide.figures else "filler",
        title=lines[0] if lines else f"Slide {slide.id}",
        bullets=lines[1:], equations=[], code=[], tables=[],
        figures=[Figure(kind="other", importance="essential", crop=c.id,
                        description="Figure from the slide (could not be analysed automatically).")
                 for c in slide.figures],
        key_terms=[],
    )
