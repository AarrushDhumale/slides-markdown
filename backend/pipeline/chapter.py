"""Chapters: an ordered group of PDFs that becomes one document per level.

Slides are addressed chapter-wide as `A·12` (2nd PDF -> B, page 12).
"""

from __future__ import annotations

import difflib
import json
import re
import string
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from backend.paths import CHAPTERS_DIR, INBOX_DIR
from backend.pipeline.ingest import FigureCrop, IngestedPdf, ingest_pdf, same_figure

SEP = "·"


def slugify(name: str) -> str:
    s = re.sub(r"[^\w\s-]", "", name).strip().lower()
    return re.sub(r"[\s_-]+", "-", s) or "chapter"


def natural_key(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def pdf_letter(index: int) -> str:
    letters = string.ascii_uppercase
    return letters[index] if index < 26 else letters[index // 26 - 1] + letters[index % 26]


def slide_id(pdf_index: int, page: int) -> str:
    return f"{pdf_letter(pdf_index)}{SEP}{page}"


def slide_file_id(sid: str) -> str:
    """`B·12` -> `B-012` (safe for filenames)."""
    letter, page = sid.split(SEP)
    return f"{letter}-{int(page):03d}"


@dataclass
class Slide:
    id: str
    pdf: IngestedPdf
    page: int
    text: str
    image_path: Path
    figures: list[FigureCrop]
    duplicate_of: str | None = None      # identical to / superseded by another slide
    # crop id -> asset name of the first identical figure in the chapter ("A-026-f1"),
    # so a diagram repeated on several slides is stored and embedded once
    canonical: dict[str, str] = field(default_factory=dict)

    def figure_path(self, fig_id: str) -> Path | None:
        crop = next((f for f in self.figures if f.id == fig_id), None)
        return self.pdf.dir / crop.path if crop else None

    def figure_asset(self, fig_id: str | None) -> str:
        """Asset name for a crop of this slide (or the whole slide when fig_id is None)."""
        own = slide_file_id(self.id)
        if fig_id is None:
            return own
        return self.canonical.get(fig_id, f"{own}-{fig_id}")


@dataclass
class Chapter:
    name: str
    pdf_paths: list[Path]

    @property
    def slug(self) -> str:
        return slugify(self.name)

    @property
    def dir(self) -> Path:
        return CHAPTERS_DIR / self.slug

    def ingest(self) -> list[IngestedPdf]:
        pdfs = [ingest_pdf(p) for p in self.pdf_paths]
        self.dir.mkdir(parents=True, exist_ok=True)
        manifest_path = self.dir / "chapter.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"created": time.time()}
        manifest.update({
            "name": self.name,
            "pdfs": [{"letter": pdf_letter(i), "path": d.source, "sha": d.sha, "pages": len(d.pages)}
                     for i, d in enumerate(pdfs)],
            "updated": time.time(),
        })
        manifest_path.write_text(json.dumps(manifest, indent=1))
        return pdfs

    def mark_generated(self, level: str) -> None:
        path = self.dir / "chapter.json"
        manifest = json.loads(path.read_text())
        manifest.setdefault("levels", {})[level] = time.time()
        path.write_text(json.dumps(manifest, indent=1))


def build_slides(pdfs: list[IngestedPdf]) -> list[Slide]:
    slides = []
    for i, pdf in enumerate(pdfs):
        for p in pdf.pages:
            slides.append(Slide(
                id=slide_id(i, p.page), pdf=pdf, page=p.page, text=p.text,
                image_path=pdf.dir / p.image, figures=list(p.figures),
            ))
    mark_builds(slides)
    mark_duplicates(slides)
    link_repeated_figures(slides)
    return slides


def link_repeated_figures(slides: list[Slide]) -> None:
    seen: list[tuple[FigureCrop, str]] = []  # (crop, asset name of first occurrence)
    for s in slides:
        for crop in s.figures:
            match = next((asset for first, asset in seen if same_figure(first, crop)), None)
            if match:
                s.canonical[crop.id] = match
            else:
                seen.append((crop, f"{slide_file_id(s.id)}-{crop.id}"))


def _title(text: str) -> str:
    return _norm(text.split("\n", 1)[0]) if text else ""


def mark_builds(slides: list[Slide], min_covered: float = 0.9) -> None:
    """Progressive 'build' slides: when slide k+1 repeats slide k (same title, k's text and figures
    all present) and adds more, k is redundant — keep only the fuller slide."""
    for a, b in zip(slides, slides[1:]):
        if a.pdf is not b.pdf or not a.text or _title(a.text) != _title(b.text):
            continue
        ta, tb = _norm(a.text), _norm(b.text)
        if len(tb) <= len(ta):
            continue
        covered = sum(m.size for m in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_matching_blocks())
        figs_kept = all(any(same_figure(fa, fb) for fb in b.figures) for fa in a.figures)
        if covered >= min_covered * len(ta) and figs_kept:
            a.duplicate_of = b.id


# ---------- near-duplicate detection (recap slides repeated across parts) ----------

def _dhash(path: Path, size: int = 16) -> int:
    img = Image.open(path).convert("L").resize((size + 1, size))
    px = img.tobytes()
    bits = 0
    for row in range(size):
        for col in range(size):
            left = px[row * (size + 1) + col]
            right = px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (left > right)
    return bits


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def mark_duplicates(slides: list[Slide], max_hamming: int = 10, min_text_ratio: float = 0.92) -> None:
    """Flag later slides that are visually AND textually near-identical to an earlier one."""
    hashes = [_dhash(s.image_path) for s in slides]
    texts = [_norm(s.text) for s in slides]
    for j in range(len(slides)):
        if slides[j].duplicate_of:
            continue  # already superseded by a build slide
        for i in range(j):
            if slides[i].duplicate_of:
                continue
            if bin(hashes[i] ^ hashes[j]).count("1") > max_hamming:
                continue
            a, b = texts[i], texts[j]
            if len(a) < 20 and len(b) < 20:
                # near-empty text layer: needs identical text AND a very close image match
                # ("Questions?" vs "Thank you" look alike as images but are different slides)
                if a != b or bin(hashes[i] ^ hashes[j]).count("1") > 2:
                    continue
            elif difflib.SequenceMatcher(None, a, b).ratio() < min_text_ratio:
                continue
            slides[j].duplicate_of = slides[i].id
            break


# ---------- inbox discovery ----------

def discover_inbox(inbox: Path = INBOX_DIR) -> list[Chapter]:
    """Each subfolder = one chapter (PDFs in natural filename order); a loose PDF = its own chapter."""
    chapters = []
    if not inbox.exists():
        return chapters
    for entry in sorted(inbox.iterdir(), key=natural_key):
        if entry.is_dir():
            pdfs = sorted(entry.glob("*.pdf"), key=natural_key)
            if pdfs:
                chapters.append(Chapter(entry.name, pdfs))
        elif entry.suffix.lower() == ".pdf":
            chapters.append(Chapter(entry.stem, [entry]))
    return chapters
