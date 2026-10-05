"""Stage 1: PDF -> page PNGs + text layer + figure crops, cached per PDF hash.

Figure pipeline per page:
1. Candidates: raster images placed on the page + vector line-art clusters
   (shapes/arrows/charts drawn natively in PowerPoint/Keynote/LaTeX).
2. Template removal: anything in the same spot on many pages (logos, banners, rules)
   is dropped *before* merging, so it can't glue itself onto a real figure.
3. Merge overlapping candidates; attach nearby small-font labels and "Figure N" captions.
4. Clean render: on a scratch copy of the page, erase body-text lines that merely cross
   the figure's bounds and blank template images, then crop, trim whitespace, and hash
   (so the same figure repeated on later slides can be embedded once).
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import median

import pymupdf as fitz
from PIL import Image, ImageChops

from backend.paths import CACHE_DIR

INGEST_VERSION = 4          # bump to invalidate cached ingests when this logic changes
RENDER_DPI = 150            # full-slide images (fed to the VLM, shown in the viewer)
FIGURE_DPI = 200            # figure crops embedded in the docs
MIN_FIG_FRAC = 0.02         # ignore regions smaller than 2% of the page area
MAX_FIG_FRAC = 0.92         # regions covering ~the whole page are backgrounds/templates
MIN_FIG_SIDE_PT = 40        # thin strips (rules, underlines, bars) are not figures
REPEAT_FRAC = 0.4           # in the same spot on >=40% of pages (and >=4 pages) -> template
MAX_FIGS_PER_PAGE = 6
LABEL_GAP_PT = 12           # labels/captions this close to a figure are part of it
CROSSING_INSIDE_MAX = 0.5   # a text line less than half inside a figure is body text crossing it
TRIM_PAD_PX = 10


@dataclass
class FigureCrop:
    id: str                         # "f1", "f2", ... (unique within the page)
    path: str                       # relative to the PDF cache dir
    bbox: list[float]               # x0, y0, x1, y1 in PDF points
    source: str                     # "raster" | "vector"
    hash: str = ""                  # perceptual hash (hex) of the rendered crop
    digests: list[str] = field(default_factory=list)  # content digests of the embedded images it shows


def same_figure(a: "FigureCrop", b: "FigureCrop", max_bits: int = 24) -> bool:
    """Same picture (identical embedded images) or visually near-identical render."""
    if a.digests and b.digests:
        return a.digests == b.digests
    return hash_distance(a.hash, b.hash) <= max_bits


@dataclass
class PageInfo:
    page: int                       # 1-based
    image: str                      # path relative to the PDF cache dir
    text: str
    figures: list[FigureCrop] = field(default_factory=list)


@dataclass
class IngestedPdf:
    sha: str
    source: str
    title: str
    pages: list[PageInfo]

    @property
    def dir(self) -> Path:
        return CACHE_DIR / self.sha


@dataclass
class Line:
    rect: fitz.Rect
    text: str
    size: float


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def dhash_hex(img: Image.Image, size: int = 16) -> str:
    g = img.convert("L").resize((size + 1, size))
    px = g.tobytes()
    bits = 0
    for row in range(size):
        for col in range(size):
            bits = (bits << 1) | (px[row * (size + 1) + col] > px[row * (size + 1) + col + 1])
    return f"{bits:0{size * size // 4}x}"


def hash_distance(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1") if a and b else 10**6


# ---------- candidates ----------

def _usable(r: fitz.Rect, page_area: float) -> bool:
    frac = r.get_area() / page_area
    return (MIN_FIG_FRAC <= frac <= MAX_FIG_FRAC
            and r.width >= MIN_FIG_SIDE_PT and r.height >= MIN_FIG_SIDE_PT)


def _raw_regions(page: fitz.Page) -> list[tuple[fitz.Rect, str]]:
    area = page.rect.get_area()
    regions: list[tuple[fitz.Rect, str]] = []
    for info in page.get_image_info():
        r = fitz.Rect(info["bbox"]) & page.rect
        if _usable(r, area):
            regions.append((r, "raster"))
    try:
        clusters = page.cluster_drawings(x_tolerance=12, y_tolerance=12)
    except Exception:  # malformed drawings shouldn't kill the ingest
        clusters = []
    for r in clusters:
        r = fitz.Rect(r) & page.rect
        if _usable(r, area):
            regions.append((r, "vector"))
    return regions


def _merge(regions: list[tuple[fitz.Rect, str]]) -> list[tuple[fitz.Rect, str]]:
    """Union overlapping regions until stable (a diagram = picture + arrows drawn over it)."""
    regions = [(fitz.Rect(r), s) for r, s in regions]
    changed = True
    while changed:
        changed = False
        out: list[tuple[fitz.Rect, str]] = []
        for r, s in regions:
            for i, (o, os_) in enumerate(out):
                if r.intersects(o):
                    out[i] = (o | r, os_ if os_ == s else "raster")
                    changed = True
                    break
            else:
                out.append((r, s))
        regions = out
    return regions


def _region_key(r: fitz.Rect) -> tuple:
    """Position fingerprint for spotting template elements repeated across pages."""
    return tuple(round(v / 15) for v in (r.x0, r.y0, r.x1, r.y1))


# ---------- text lines ----------

def _lines(page: fitz.Page) -> list[Line]:
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(s["text"] for s in line["spans"]).strip()
            if text:
                sizes = [s["size"] for s in line["spans"] if s["text"].strip()]
                out.append(Line(fitz.Rect(line["bbox"]), text, max(sizes) if sizes else 0))
    return out


def _line_key(line: Line) -> tuple:
    return (*_region_key(line.rect), re.sub(r"\d+", "#", line.text))


def _body_size(lines: list[Line]) -> float:
    sizes = [ln.size for ln in lines for _ in range(min(len(ln.text), 80))]
    return median(sizes) if sizes else 0


def _inside_frac(line: Line, r: fitz.Rect) -> float:
    a = line.rect.get_area()
    return (line.rect & r).get_area() / a if a else 0


def _attach_labels(r: fitz.Rect, lines: list[Line], body: float) -> fitz.Rect:
    """Grow a figure to include small labels and 'Figure N' captions sitting just outside it."""
    grown = fitz.Rect(r)
    for ln in lines:
        if ln.rect.intersects(r):
            continue
        near = fitz.Rect(r.x0 - LABEL_GAP_PT, r.y0 - LABEL_GAP_PT, r.x1 + LABEL_GAP_PT, r.y1 + LABEL_GAP_PT)
        if not ln.rect.intersects(near):
            continue
        caption = re.match(r"(fig(ure)?\.?|table)\s*\d", ln.text, re.I)
        small_label = ln.size <= 0.85 * body and len(ln.text.split()) <= 8
        if caption or small_label:
            grown |= ln.rect
    return grown


# ---------- rendering ----------

def _trim_whitespace(img: Image.Image) -> Image.Image:
    rgb = img.convert("RGB")
    corners = [rgb.getpixel(p) for p in ((0, 0), (rgb.width - 1, 0), (0, rgb.height - 1),
                                        (rgb.width - 1, rgb.height - 1))]
    bg = Counter(corners).most_common(1)[0][0]
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, bg)).convert("L")
    bbox = diff.point(lambda v: 255 if v > 24 else 0).getbbox()
    if not bbox:
        return rgb
    x0, y0, x1, y1 = bbox
    return rgb.crop((max(0, x0 - TRIM_PAD_PX), max(0, y0 - TRIM_PAD_PX),
                     min(rgb.width, x1 + TRIM_PAD_PX), min(rgb.height, y1 + TRIM_PAD_PX)))


def _clean_page(doc: fitz.Document, pno: int, erase_text: list[fitz.Rect],
                template_images: list[fitz.Rect]) -> tuple[fitz.Document, fitz.Page]:
    """Scratch copy of one page with stray text removed and template images (banners, logos,
    rules) deleted as objects, so a figure underneath keeps all of its own pixels."""
    tmp = fitz.open()
    tmp.insert_pdf(doc, from_page=pno, to_page=pno)
    page = tmp[0]
    if template_images:
        doomed = {info["xref"] for info in page.get_image_info(xrefs=True)
                  if info["xref"] and any(_same_rect(fitz.Rect(info["bbox"]), t) for t in template_images)}
        for xref in doomed:
            page.delete_image(xref)
    if erase_text:
        for r in erase_text:
            page.add_redact_annot(r)
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                              graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                              text=fitz.PDF_REDACT_TEXT_REMOVE)
    return tmp, page


def _same_rect(a: fitz.Rect, b: fitz.Rect, tol: float = 2) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def _render_crop(page: fitz.Page, clip: fitz.Rect) -> Image.Image:
    pix = page.get_pixmap(clip=clip, dpi=FIGURE_DPI)
    return _trim_whitespace(Image.open(io.BytesIO(pix.tobytes("png"))))


# ---------- main ----------

@dataclass
class DetectedFigure:
    rect: fitz.Rect
    source: str
    erase_text: list[fitz.Rect]
    template_images: list[fitz.Rect]
    digests: list[str]


def detect_figures(doc: fitz.Document) -> tuple[list[list[DetectedFigure]], list[str]]:
    """Per page: detected figures, and the page's body text (template lines like footers and
    page numbers removed)."""
    raws = [_raw_regions(p) for p in doc]
    lines = [_lines(p) for p in doc]
    infos = [p.get_image_info(hashes=True) for p in doc]
    placements = [[fitz.Rect(i["bbox"]) for i in inf] for inf in infos]
    threshold = max(4, REPEAT_FRAC * len(doc))
    region_counts = Counter(k for rs in raws for k in {_region_key(r) for r, _ in rs})
    template = {k for k, n in region_counts.items() if n >= threshold}
    # every repeated image placement, however small (banners, logos, coloured rules)
    img_counts = Counter(k for ps in placements for k in {_region_key(r) for r in ps})
    template_imgs = {k for k, n in img_counts.items() if n >= threshold}
    line_counts = Counter(k for ls in lines for k in {_line_key(ln) for ln in ls})
    template_lines = {k for k, n in line_counts.items() if n >= threshold}

    result, texts = [], []
    for page, rs, ls, ps, inf in zip(doc, raws, lines, placements, infos):
        area = page.rect.get_area()
        tmpl_rects = [r for r in ps if _region_key(r) in template_imgs]
        content = [(r, s) for r, s in rs if _region_key(r) not in template]
        body_lines = [ln for ln in ls if _line_key(ln) not in template_lines]
        body = _body_size(body_lines)
        regions = _merge([(_attach_labels(r, body_lines, body), s) for r, s in _merge(content)])
        regions = [(r, s) for r, s in regions if _usable(r, area)]
        regions.sort(key=lambda rs_: -rs_[0].get_area())
        regions = sorted(regions[:MAX_FIGS_PER_PAGE], key=lambda rs_: (rs_[0].y0, rs_[0].x0))
        page_out = []
        for r, s in regions:
            # Erase any line that only clips the figure's bounds. For pasted pictures (labels are
            # baked into the pixels) also erase slide titles/bullets lying in the picture's margin;
            # small annotation text stays.
            crossing = [ln.rect for ln in ls if ln.rect.intersects(r) and (
                _inside_frac(ln, r) < CROSSING_INSIDE_MAX or (s == "raster" and ln.size >= 0.9 * body))]
            digests = sorted({i["digest"].hex() for i in inf
                              if i.get("digest") and _region_key(fitz.Rect(i["bbox"])) not in template_imgs
                              and (fitz.Rect(i["bbox"]) & r).get_area() >= 0.5 * fitz.Rect(i["bbox"]).get_area()})
            page_out.append(DetectedFigure(r, s, crossing, [t for t in tmpl_rects if t.intersects(r)], digests))
        result.append(page_out)
        texts.append("\n".join(ln.text for ln in body_lines))
    return result, texts


def ingest_pdf(pdf: Path) -> IngestedPdf:
    sha = file_sha(pdf)
    out = CACHE_DIR / sha
    meta_path = out / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        if meta.get("version") == INGEST_VERSION:
            pages = [PageInfo(p["page"], p["image"], p["text"], [FigureCrop(**f) for f in p["figures"]])
                     for p in meta["pages"]]
            return IngestedPdf(meta["sha"], meta["source"], meta["title"], pages)

    (out / "pages").mkdir(parents=True, exist_ok=True)
    (out / "figures").mkdir(exist_ok=True)
    pages: list[PageInfo] = []
    with fitz.open(pdf) as doc:
        detected, texts = detect_figures(doc)
        for i, (page, regions, text) in enumerate(zip(doc, detected, texts), start=1):
            img = f"pages/{i:03d}.png"
            page.get_pixmap(dpi=RENDER_DPI).save(out / img)
            figures = []
            for n, fig in enumerate(regions, start=1):
                tmp, clean = _clean_page(doc, i - 1, fig.erase_text, fig.template_images)
                try:
                    crop = _render_crop(clean, fig.rect & clean.rect)
                finally:
                    tmp.close()
                name = f"figures/p{i:03d}-f{n}.png"
                crop.save(out / name)
                figures.append(FigureCrop(f"f{n}", name, [round(v, 1) for v in fig.rect], fig.source,
                                          dhash_hex(crop), fig.digests))
            pages.append(PageInfo(i, img, text, figures))
        title = (doc.metadata or {}).get("title") or pdf.stem

    result = IngestedPdf(sha, str(pdf.resolve()), title, pages)
    meta_path.write_text(json.dumps({"version": INGEST_VERSION, **asdict(result)}, indent=1))
    return result
