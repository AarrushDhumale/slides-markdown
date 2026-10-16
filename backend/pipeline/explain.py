""""Explain this": a short, streamed explanation of a highlighted passage or a figure in a generated doc,
grounded in the section's source slides. Context is built here (server side) from the doc and the
slide extracts, so a client can only point at things, never inject its own source material."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from backend.llm.client import image_part
from backend.paths import PROMPTS_DIR
from backend.pipeline.chapter import SEP

ANCHOR_RE = re.compile(r"<!-- slides ([^>]*?) -->")
IMAGE_REF_RE = re.compile(rf"^(assets/[\w.-]+\.png|slide:[A-Z]+{SEP}\d+)$")
MAX_SECTION_CHARS = 6000
MAX_SELECTION_CHARS = 1500
MAX_TURNS = 3            # follow-up questions per explanation
MAX_REPLY_TOKENS = 450


class ExplainError(ValueError):
    """The request points at something that doesn't exist (bad section, image or selection)."""


@dataclass
class ExplainRequest:
    section: int                                   # index of the `<!-- slides … -->` anchor in the doc
    selection: str | None = None                   # highlighted text
    image: str | None = None                       # "assets/A-003-f1.png" or "slide:A·4"
    history: list[dict] = field(default_factory=list)   # earlier [{role, content}] turns of this popover
    simpler: bool = False


def expand_ids(spec: str) -> list[str]:
    """'A·4-6, B·2' -> ['A·4', 'A·5', 'A·6', 'B·2'] (inverse of assemble.compress_ids)."""
    out = []
    for part in spec.split(","):
        part = part.strip()
        m = re.fullmatch(rf"([A-Z]+){SEP}(\d+)(?:-(\d+))?", part)
        if not m:
            continue
        letter, a, b = m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))
        out += [f"{letter}{SEP}{p}" for p in range(a, b + 1)]
    return out


def split_sections(md: str) -> list[tuple[list[str], str]]:
    """[(slide ids, section markdown)] for each slide anchor, in document order."""
    marks = list(ANCHOR_RE.finditer(md))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(md)
        out.append((expand_ids(m.group(1)), md[m.end():end].strip()))
    return out


def _slides_json(slide_ids: list[str], extracts: dict[str, dict]) -> str:
    rows = []
    for sid in slide_ids:
        ex = extracts.get(sid)
        if ex:
            rows.append({"slide": sid, **{k: v for k, v in ex.items() if k != "slide_role" and v}})
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))[:12000]


def build_messages(doc_md: str, extracts: dict[str, dict], req: ExplainRequest,
                   resolve_image: Callable[[str], Path | None]) -> list[dict]:
    sections = split_sections(doc_md)
    if not 0 <= req.section < len(sections):
        raise ExplainError(f"section {req.section} not found (doc has {len(sections)})")
    if not req.selection and not req.image:
        raise ExplainError("nothing selected")
    slide_ids, notes = sections[req.section]
    selection = (req.selection or "").strip()[:MAX_SELECTION_CHARS]
    image_path = None
    if req.image:
        if not IMAGE_REF_RE.match(req.image):
            raise ExplainError(f"bad image reference {req.image!r}")
        image_path = resolve_image(req.image)
        if image_path is None or not image_path.exists():
            raise ExplainError(f"image {req.image!r} not found")
        if req.image.startswith("slide:") and req.image[6:] not in slide_ids:
            slide_ids = slide_ids + [req.image[6:]]

    ask = (f"SELECTION (highlighted text):\n<<<\n{selection}\n>>>" if selection else
           f"SELECTION: the attached figure ({req.image}).")
    if req.simpler and not req.history:
        ask += "\n\nExplain it as simply as you can."
    text = (f"SLIDES:\n{_slides_json(slide_ids, extracts)}\n\n"
            f"NOTES:\n<<<\n{notes[:MAX_SECTION_CHARS]}\n>>>\n\n{ask}")
    content: str | list[dict] = text
    if image_path is not None:
        content = [{"type": "text", "text": text}, image_part(image_path)]
    messages = [{"role": "system", "content": (PROMPTS_DIR / "explain.md").read_text()},
                {"role": "user", "content": content}]
    turns = [t for t in req.history if t.get("role") in ("user", "assistant") and isinstance(t.get("content"), str)]
    if sum(t["role"] == "user" for t in turns) > MAX_TURNS:
        raise ExplainError(f"at most {MAX_TURNS} follow-up questions per explanation")
    messages += [{"role": t["role"], "content": t["content"][:2000]} for t in turns]
    if req.history and req.simpler:
        messages.append({"role": "user", "content": "Explain that more simply."})
    if messages[-1]["role"] != "user":
        raise ExplainError("a follow-up must end with the student's question")
    return messages


def explain(llm, messages: list[dict]) -> Iterator[str]:
    """Stream the explanation (llm: an LLM or ChainLLM with `.stream`)."""
    yield from llm.stream(messages, max_tokens=MAX_REPLY_TOKENS, temperature=0.3)
