import io
import os
import tempfile
from pathlib import Path

# Must be set before backend.paths is imported.
os.environ["S2M_DATA"] = tempfile.mkdtemp(prefix="s2m-test-")

import pymupdf as fitz  # noqa: E402
import pytest  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

W, H = 720, 405  # 16:9 slide in points


def _diagram_png() -> bytes:
    img = Image.new("RGB", (600, 300), "white")
    d = ImageDraw.Draw(img)
    for i, label in enumerate(["Input", "Process", "Output"]):
        x = 30 + i * 200
        d.rectangle([x, 110, x + 140, 190], outline="black", width=4)
        d.text((x + 40, 140), label, fill="black")
        if i < 2:
            d.line([x + 140, 150, x + 200, 150], fill="black", width=4)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _logo_png() -> bytes:
    img = Image.new("RGB", (200, 200), "navy")
    ImageDraw.Draw(img).ellipse([30, 30, 170, 170], fill="gold")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


LOGO = _logo_png()


def _vector_diagram(page):
    """A recursion tree drawn with native shapes (no raster image), like PowerPoint SmartArt."""
    shape = page.new_shape()
    nodes = {"T(n)": (470, 130), "T(n/2) a": (390, 230), "T(n/2) b": (550, 230)}
    for label, (x, y) in nodes.items():
        shape.draw_rect(fitz.Rect(x - 45, y - 20, x + 45, y + 20))
    shape.draw_line((470, 150), (390, 210))
    shape.draw_line((470, 150), (550, 210))
    shape.finish(color=(0, 0, 0), width=2)
    shape.commit()
    for label, (x, y) in nodes.items():
        page.insert_text((x - 30, y + 5), label.split()[0], fontsize=12)


def _slide(doc, title, lines=(), image=None, vector=False):
    page = doc.new_page(width=W, height=H)
    page.insert_text((40, 60), title, fontsize=28)
    page.insert_image(fitz.Rect(620, 10, 710, 100), stream=LOGO)  # template logo on every slide
    y = 110
    for line in lines:
        page.insert_text((50, y), line, fontsize=16)
        y += 28
    if image:
        page.insert_image(fitz.Rect(360, 150, 700, 380), stream=image)
    if vector:
        _vector_diagram(page)


RECAP = ("Recap: Big-O", ["O(n) means time grows linearly with input size n",
                          "Binary search runs in O(log n) on sorted arrays",
                          "Constants and lower-order terms are dropped"])


@pytest.fixture(scope="session")
def chapter_pdfs() -> list[Path]:
    out = Path(os.environ["S2M_DATA"]) / "fixtures"
    out.mkdir(parents=True, exist_ok=True)

    a = fitz.open()
    _slide(a, "CS F211: Data Structures", ["Lecture 3"])
    _slide(a, *RECAP)
    _slide(a, "Pipeline model", ["Every algorithm maps input to output"], image=_diagram_png())
    _slide(a, "Linear search", ["def find(xs, t):", "    for i, x in enumerate(xs):",
                                "        if x == t: return i", "    return -1"])
    _slide(a, "Questions?")
    a.save(out / "part1.pdf")

    b = fitz.open()
    _slide(b, *RECAP)  # repeated recap at the start of part 2
    _slide(b, "Master theorem", ["T(n) = a T(n/b) + f(n)", "Case 1: f(n) = O(n^(log_b a - e))"])
    _slide(b, "Recursion tree", ["Each level splits the problem in two"], vector=True)
    _slide(b, "Recursion tree (cont.)", ["Cost per level: n"])
    _slide(b, "Pipeline model revisited", ["The same diagram, shown again"], image=_diagram_png())
    _slide(b, "Thank you")
    b.save(out / "part2.pdf")
    return [out / "part1.pdf", out / "part2.pdf"]
