"""Stage 5: stitch sections into one Markdown doc, copy slide images, sanity-check math."""

from __future__ import annotations

import re
from collections import Counter
import shutil
from pathlib import Path

from backend.pipeline.chapter import SEP, Chapter, Slide, slide_file_id
from backend.pipeline.extract import SlideExtract, figure_assets
from backend.pipeline.outline import GROUPED_MODES, MODE_SUFFIX, Outline

LEVEL_LABELS = {"proficient": "Proficient", "learner": "Learner", "donkey": "Donkey", "notes": "Notes",
                "summary": "Summary"}
ASSET_RE = re.compile(r"assets/([A-Z]+-\d{3}(?:-f\d+)?)\.png")
FENCE_RE = re.compile(r"^(```|~~~)")


def compress_ids(ids: list[str]) -> str:
    """['A·4','A·5','A·6','B·2'] -> 'A·4-6, B·2'"""
    out, run = [], []
    for sid in ids:
        letter, page = sid.split(SEP)
        if run and run[0][0] == letter and int(page) == run[-1][1] + 1:
            run.append((letter, int(page)))
            continue
        if run:
            out.append(run)
        run = [(letter, int(page))]
    if run:
        out.append(run)
    return ", ".join(f"{r[0][0]}{SEP}{r[0][1]}" + (f"-{r[-1][1]}" if len(r) > 1 else "") for r in out)


def anchor(title: str) -> str:
    s = re.sub(r"[^\w\s-]", "", title.strip().lower())
    return re.sub(r"\s", "-", s)


def _prose_lines(md: str):
    """Yield (index, line) for lines outside fenced code blocks."""
    in_fence = False
    for i, line in enumerate(md.splitlines()):
        if FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            continue
        if not in_fence:
            yield i, line


def check_markdown(md: str) -> list[str]:
    """Cheap structural checks for things that break rendering (KaTeX, fences)."""
    problems = []
    if sum(1 for line in md.splitlines() if FENCE_RE.match(line.strip())) % 2:
        problems.append("unclosed code fence")
    prose = "\n".join(line for _, line in _prose_lines(md))
    prose = re.sub(r"`[^`\n]*`", "", prose)  # inline code
    if prose.count("$$") % 2:
        problems.append("unbalanced $$ display math")
    inline = re.sub(r"\$\$.*?\$\$", "", prose, flags=re.S).replace("\\$", "")
    if inline.count("$") % 2:
        problems.append("unbalanced $ inline math")
    for m in re.finditer(r"\$\$(.*?)\$\$|\$([^$\n]+)\$", prose, flags=re.S):
        expr = m.group(1) or m.group(2) or ""
        expr = expr.replace("\\{", "").replace("\\}", "")
        if expr.count("{") != expr.count("}"):
            problems.append(f"unbalanced braces in math: {expr.strip()[:60]}")
    return problems


MAX_PARAGRAPH_CHARS = 2500
MAX_UNPUNCTUATED_WORDS = 60
MAX_NGRAM_REPEATS = 6


def _prose_paragraphs(md: str) -> list[str]:
    """Prose paragraphs/lines, skipping code fences, tables, images and math blocks."""
    keep = [line for _, line in _prose_lines(md)
            if not TABLE_ROW_RE.match(line) and not line.lstrip().startswith(("![", "$$", "<"))]
    return [p for p in re.split(r"\n\s*\n", "\n".join(keep)) if p.strip()]


SELF_CORRECTION_RE = re.compile(
    r"\b(wait[,—!.]|actually,? no\b|no, let'?s\b|let me (re-?)?(check|look|re-?read|calculate)|"
    r"let'?s re-?(check|read|calculate|examine|look)|hmm[,.]|on second thought)", re.I)
MAX_SELF_CORRECTIONS = 1


def degeneration(md: str) -> str | None:
    """Detect runaway generation (word-salad, loops, visible thinking) that no reader should see.
    None if clean."""
    paras = _prose_paragraphs(md)
    corrections = sum(len(SELF_CORRECTION_RE.findall(p)) for p in paras)
    if corrections > MAX_SELF_CORRECTIONS:
        return f"thinks out loud ({corrections} self-corrections like 'Wait, let's re-check')"
    for p in paras:
        if len(p) > MAX_PARAGRAPH_CHARS:
            return f"run-on paragraph of {len(p)} chars"
        for line in p.splitlines():
            run = best = 0
            for w in line.split():
                run = 0 if re.search(r"[.!?;:)\]]$|^[-*>|#]", w) else run + 1
                best = max(best, run)
            if best >= MAX_UNPUNCTUATED_WORDS:
                return f"{best} words without punctuation"
    words = " ".join(paras).lower().split()
    grams = Counter(zip(*(words[i:] for i in range(5))))
    if grams and (top := grams.most_common(1)[0])[1] >= MAX_NGRAM_REPEATS:
        return f"phrase repeated {top[1]}x: '{' '.join(top[0])}'"
    return None


COVERAGE_STOPWORDS = {
    "this", "that", "with", "from", "into", "using", "used", "uses", "what", "when", "which", "their", "there",
    "these", "those", "have", "over", "some", "more", "than", "then", "each", "other", "about", "example",
    "examples", "introduction", "overview", "slide", "lecture", "part", "continued", "cont", "definition",
    "definitions", "properties", "property", "problem", "problems", "basic", "basics", "summary", "recap"}
MIN_COVERED = 0.4


def missing_slides(md: str, slide_ids: list[str], extracts: dict[str, SlideExtract],
                   skip_roles: tuple = ("title", "agenda", "filler", "references")) -> list[str]:
    """Slides whose title / key-term words barely appear in the written text (likely skipped)."""
    text = md.lower()
    missing = []
    for sid in slide_ids:
        ex = extracts.get(sid)
        if ex is None or ex.slide_role in skip_roles:
            continue
        words = {w for w in re.findall(r"[a-z][a-z\-]{3,}", (ex.title + " " + " ".join(ex.key_terms)).lower())
                 if w not in COVERAGE_STOPWORDS}
        if len(words) < 2:
            continue
        if sum(w in text for w in words) / len(words) < MIN_COVERED:
            missing.append(sid)
    return missing


def strip_degenerate(md: str) -> str:
    """Last resort: drop repeated list items and offending paragraphs, keep everything else."""
    lines, seen, prose = [], set(), {i for i, _ in _prose_lines(md)}
    for i, line in enumerate(md.splitlines()):
        key = line.strip()
        if i in prose and re.match(r"^([-*+]|\d+\.)\s+\S", key):
            if key in seen:
                continue  # an identical bullet already appeared
            seen.add(key)
        lines.append(line)
    md = "\n".join(lines)
    out = []
    for block in re.split(r"(\n\s*\n)", md):
        if block.strip() and degeneration(block) and not block.lstrip().startswith(("```", "|", "![")):
            continue
        out.append(block)
    return "".join(out)


def normalize_section(md: str, title: str) -> str:
    lines = md.strip().splitlines()
    prose_idx = sorted(i for i, _ in _prose_lines(md.strip()))
    first_idx = next((i for i in prose_idx if lines[i].strip()), None)
    for i in prose_idx:
        if re.match(r"^# ", lines[i]):  # H1 as the section title -> H2; stray H1 later -> H3
            lines[i] = ("##" if i == first_idx else "###") + lines[i][1:]
    first = lines[first_idx] if first_idx is not None else ""
    if not first.startswith("## "):
        lines.insert(0, f"## {title}\n")
    md = unfence_tables(unquote_details("\n".join(lines).strip()))
    md = quote_callout_lists(tighten_inline_math(md))
    md = shorten_alt_text(drop_slide_references(md))
    # Markdown (incl. math) inside <details> only renders when separated from the HTML tags by blank lines.
    md = re.sub(r"(</summary>)[ \t]*\n(?!\s*\n)", r"\1\n\n", md)
    md = re.sub(r"\n(?<!\n\n)([ \t]*</details>)", r"\n\n\1", md)
    return md


SLIDE_REF = r"slides?\s+[A-Z]{1,2}\s*·\s*\d+(?:\s*[-–—]\s*(?:[A-Z]{1,2}\s*·\s*)?\d+)?"


def drop_slide_references(md: str) -> str:
    """Writers leak source ids despite instructions: drop '**Slide A·104 Analysis**' lines and
    '(Slides A·106–A·108)' asides from prose (code is untouched)."""
    lines = md.splitlines()
    keep = []
    prose = {i for i, _ in _prose_lines(md)}
    for i, line in enumerate(lines):
        if i in prose:
            if re.fullmatch(rf"\s*(#+\s*)?\**\s*{SLIDE_REF}[^*\n]{{0,40}}\**\s*:?\s*", line, flags=re.I):
                continue
            line = re.sub(rf"\s*\(\s*{SLIDE_REF}\s*\)", "", line, flags=re.I)
            line = re.sub(rf"\s+(?:in|on|from|of|see)\s+(?:the\s+)?{SLIDE_REF}\b", "", line, flags=re.I)
        keep.append(line)
    return "\n".join(keep)


def shorten_alt_text(md: str, limit: int = 100) -> str:
    """Image alt text should be a short label, not the whole figure description."""
    def fix(m: re.Match) -> str:
        alt = m.group(1)
        if len(alt) > limit:
            alt = alt.split(". ")[0]
            alt = alt if len(alt) <= limit else alt[:limit].rsplit(" ", 1)[0] + "…"
        return f"![{alt}]({m.group(2)})"
    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", fix, md)


CLOSING_HEADING_RE = re.compile(r"^#{2,6}\s*(check your understanding|key takeaways|remember|summary)\b", re.I)
CLOSING_QUOTE_RE = re.compile(r"^>\s*\*\*(check your understanding|key takeaways|remember)\b", re.I)


def strip_closing_blocks(md: str) -> str:
    """Remove end-of-section quiz / takeaways / remember blocks (for parts that aren't a chapter's last)."""
    lines, out, skipping, quote = md.splitlines(), [], False, False
    for i, line in enumerate(lines):
        if CLOSING_HEADING_RE.match(line):
            skipping, quote = True, False
            continue
        if CLOSING_QUOTE_RE.match(line):
            skipping, quote = True, True
            continue
        if skipping:
            if quote and not line.startswith(">") and line.strip():
                skipping = False          # the quoted block ended
            elif not quote and re.match(r"^#{1,4} ", line):
                skipping = False          # next heading of the section
            else:
                continue
        out.append(line)
    return "\n".join(out).rstrip()


def demote_headings(md: str) -> str:
    """Shift every heading one level down (## -> ###), leaving code blocks alone."""
    lines = md.splitlines()
    for i, line in _prose_lines(md):
        if re.match(r"^#{2,5} ", line):
            lines[i] = "#" + line
    return "\n".join(lines)


def unquote_details(md: str) -> str:
    """`<details>` inside a blockquote breaks in most renderers (blank lines end the quote):
    pull every line of such a details block out of the quote."""
    out, inside = [], False
    for line in md.splitlines():
        quoted = re.match(r"^\s*>\s?(.*)$", line)
        body = quoted.group(1) if quoted else line
        if quoted and "<details" in body:
            inside = True
        if quoted and inside:
            line = body
        if "</details>" in body:
            inside = False
        out.append(line)
    return "\n".join(out)


def tighten_inline_math(md: str) -> str:
    """`$ x $` -> `$x$`: many renderers (GitHub, Obsidian) ignore inline math padded with spaces."""
    lines = md.splitlines()
    for i, _ in _prose_lines(md):
        # walk the delimiters in order so a closing `$` is never paired with the next opening one
        parts = re.split(r"(?<!\\)(\$\$|\$)", lines[i])
        out, in_math = [], False
        for part in parts:
            if part == "$":
                in_math = not in_math
            elif in_math:
                part = part.strip(" \t")
            out.append(part)
        if not in_math:  # unbalanced line: leave it alone
            lines[i] = "".join(out)
    return "\n".join(lines)


def quote_callout_lists(md: str) -> str:
    """`> **Key takeaways:**` followed by unquoted list items -> keep the list inside the quote."""
    out, in_callout = [], False
    for line in md.splitlines():
        if re.match(r"^>\s*\*\*[^*]+:?\*\*:?\s*$", line):
            in_callout = True
        elif in_callout and re.match(r"^\s*([-*+]|\d+\.)\s", line):
            line = "> " + line
        elif not line.startswith(">"):
            in_callout = False
        out.append(line)
    return "\n".join(out)


TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


def unfence_tables(md: str) -> str:
    """Writers sometimes wrap a Markdown table in ``` fences, which renders it as raw text."""
    def fix(m: re.Match) -> str:
        body = m.group(2)
        rows = [r for r in body.splitlines() if r.strip()]
        if rows and all(TABLE_ROW_RE.match(r) for r in rows) and any(re.search(r"\|\s*:?-{3,}", r) for r in rows):
            return body.strip("\n")
        return m.group(0)
    return re.sub(r"```(markdown|md|text)?[ \t]*\n(.*?)\n```", fix, md, flags=re.S)


CLOSING_BLOCK_RE = re.compile(
    r"^(>\s*)?(\*\*|#{2,4}\s*)(Check your understanding|Key takeaways|Remember|Summary)\b", re.I | re.M)


def insert_before_closing(md: str, block: str) -> str:
    """Insert content before a section's closing quiz/takeaways/summary block, else append.
    Never above the section's own heading (a part may be *titled* e.g. "Summary of …")."""
    first_line_end = md.find("\n") + 1 if md.startswith("#") else 0
    m = CLOSING_BLOCK_RE.search(md, first_line_end)
    if not m:
        return md + "\n\n" + block
    return md[:m.start()] + block + "\n\n" + md[m.start():]


def resolve_asset(name: str, by_file_id: dict[str, Slide]) -> Path | None:
    """`A-003` -> full slide PNG, `A-003-f2` -> that figure crop; None if unknown."""
    m = re.fullmatch(r"([A-Z]+-\d{3})(?:-(f\d+))?", name)
    if not m or m.group(1) not in by_file_id:
        return None
    slide = by_file_id[m.group(1)]
    return slide.figure_path(m.group(2)) if m.group(2) else slide.image_path


def ensure_essential_figures(md: str, section_slides: list[str], extracts: dict[str, SlideExtract],
                             slides_by_id: dict[str, Slide], shown_earlier: set[str] = frozenset()) -> str:
    """Append any essential figure the writer left out, so image-heavy slides never lose content.
    Figures already shown in an earlier section (same diagram repeated on later slides) are skipped."""
    missing, seen = [], set()
    for sid in section_slides:
        if sid not in extracts:
            continue
        for asset, fig in figure_assets(slides_by_id[sid], extracts[sid]):
            if (fig.importance == "essential" and asset not in md
                    and asset not in shown_earlier and asset not in seen):
                seen.add(asset)
                missing.append((asset, fig))
    for asset, fig in missing:
        alt = re.sub(r"[\[\]\n]", " ", fig.description.split(". ")[0])[:120]
        md = insert_before_closing(md, f"![{alt}]({asset})\n\n*Figure: {fig.description}*")
    return md


IMAGE_LINE_RE = re.compile(r"!\[[^\]]*\]\((assets/[A-Z]+-\d{3}(?:-f\d+)?\.png)\)[ \t]*\n?")


def drop_repeat_images(md: str, shown_earlier: set[str] | dict[str, str]) -> str:
    """Keep only the first embed of each image across the document (writers repeat diagrams).
    With a {asset: heading} map, a repeat becomes a link to where the diagram was shown."""
    where = shown_earlier if isinstance(shown_earlier, dict) else {}
    seen = set(shown_earlier)

    def keep_first(m: re.Match) -> str:
        if m.group(1) in seen:
            h = where.get(m.group(1))
            return f"*(Same diagram as shown earlier in [{h}](#{anchor(h)}).)*\n" if h else ""
        seen.add(m.group(1))
        return m.group(0)

    return IMAGE_LINE_RE.sub(keep_first, md)


def assemble(chapter: Chapter, outline: Outline, sections_md: list[str], level: str,
             slides: list[Slide], extracts: dict[str, SlideExtract] | None = None,
             takeaways: str = "", exam_tags: dict[int, list[str]] | None = None,
             stars: set[int] = frozenset()) -> Path:
    by_file_id = {slide_file_id(s.id): s for s in slides}
    assets = chapter.dir / "assets"
    assets.mkdir(exist_ok=True)

    sources = ", ".join(f"**{s.id.split(SEP)[0]}** = {s.pdf.title}" for s in slides if s.page == 1)
    slides_by_id = {s.id: s for s in slides}
    shown: dict[str, str] = {}  # asset -> heading of the section that first showed it
    body, headings = [], []
    starred: set[str] = set()   # headings of high-yield sections, starred in the contents
    for idx, (sec, md) in enumerate(zip(outline.sections, sections_md)):
        md = normalize_section(md, sec.title)
        if outline.mode == "slides":  # fixed "Slide N · Title" headings: other sections link to them
            md = re.sub(r"\A## .*", lambda _: f"## {sec.title}", md, count=1)
        if degeneration(md):  # final guard, also covers sections cached before a detector change
            md = strip_degenerate(md)
        if extracts:
            md = shorten_alt_text(ensure_essential_figures(md, sec.slides, extracts, slides_by_id, shown))
        # the TOC follows the heading actually written (writers retitle merged sections)
        if not md.startswith("## "):  # every part needs its heading, whatever clean-up removed
            md = f"## {sec.title}\n\n{md.lstrip()}"
        heading = re.match(r"## (.+)", md).group(1).strip()
        if exam_tags and exam_tags.get(idx):   # past papers: which exams asked about this topic
            from backend.pipeline.papers import exam_tag_line
            md = re.sub(r"\A(#{2,3} .*\n)\n*", lambda m: m.group(1) + "\n" + exam_tag_line(exam_tags[idx], idx in stars) + "\n\n", md, count=1)
            if idx in stars:
                starred.add(heading)
        md = drop_repeat_images(md, shown)
        for n in ASSET_RE.findall(md):
            shown.setdefault(f"assets/{n}.png", heading)
        if outline.mode in GROUPED_MODES:
            if sec.part_index < sec.part_count:   # the chapter continues: no quiz/takeaways mid-chapter
                md = strip_closing_blocks(md)
            if sec.part_count == 1:               # the part *is* the chapter: its heading would repeat it
                md = re.sub(r"\A## .*\n*", "", md, count=1)
            else:
                md = demote_headings(md)
            if sec.group_intro or not headings or headings[-1][2] != sec.group:
                body.append(f"---\n\n## {sec.group}\n\n{sec.group_intro}".rstrip())
                headings.append((2, sec.group, sec.group))
        headings.append((3 if outline.mode in GROUPED_MODES else 2, heading, sec.group))
        body.append(f"{'' if outline.mode in GROUPED_MODES else '---' + chr(10) + chr(10)}"
                    f"<!-- slides {compress_ids(sec.slides)} -->\n{md}")
    if outline.mode == "chapters":  # chapter-wise doc: the contents list shows chapters only
        headings = [hd for hd in headings if hd[0] == 2]
    toc = "\n".join(f"{'  ' * (lvl - 2)}- [{h}](#{anchor(h)})" + (" ★" if h in starred else "")
                    for lvl, h, _ in headings)
    parts = [
        f"# {outline.chapter_title}",
        f"> **Level:** {LEVEL_LABELS[level]} · **Sources:** {sources}",
        outline.overview,
        *([f"> **Key takeaways**\n>\n" + "\n".join(f"> {ln}" for ln in takeaways.splitlines())]
          if takeaways else []),
        "## Contents\n\n" + toc,
        *body,
    ]
    doc = "\n\n".join(parts) + "\n"

    for name in set(ASSET_RE.findall(doc)):
        src = resolve_asset(name, by_file_id)
        if src and src.exists():
            shutil.copyfile(src, assets / f"{name}.png")
        else:  # writer invented a path: drop the broken image rather than ship it
            doc = re.sub(rf"!\[[^\]]*\]\(assets/{re.escape(name)}\.png\)\n?", "", doc)

    out = chapter.dir / f"{level}{MODE_SUFFIX[outline.mode]}.md"
    out.write_text(doc)
    return out
