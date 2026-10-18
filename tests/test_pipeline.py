"""Offline tests: everything except real model calls (a fake LLM stands in)."""

import json
import re

import pytest

from backend.pipeline.assemble import (check_markdown, compress_ids, drop_repeat_images,
                                       ensure_essential_figures, normalize_section, resolve_asset)
from backend.pipeline.chapter import Chapter, Slide, build_slides, slide_file_id
from backend.pipeline.extract import Figure, SlideExtract, figure_assets
from backend.pipeline.outline import Outline, Section, repair


def _extract(title, role="content", figures=()):
    return SlideExtract(slide_role=role, title=title, bullets=[f"about {title}"], equations=[],
                        code=[], tables=[], figures=list(figures), key_terms=[])


def _fig(importance="essential", crop="f1", desc="input -> process -> output"):
    return Figure(kind="diagram", description=desc, importance=importance, crop=crop)


class _StubSlide:
    """Just enough of Slide for figure-asset naming."""
    figure_asset = Slide.figure_asset

    def __init__(self, sid, canonical=None):
        self.id, self.canonical = sid, canonical or {}


# ---------- ingest / chapter ----------

def test_ingest_and_ids(chapter_pdfs):
    pdfs = Chapter("Week 3", chapter_pdfs).ingest()
    assert [len(p.pages) for p in pdfs] == [5, 6]
    slides = build_slides(pdfs)
    assert [s.id for s in slides] == ["A·1", "A·2", "A·3", "A·4", "A·5",
                                      "B·1", "B·2", "B·3", "B·4", "B·5", "B·6"]
    assert not any("#" in s.text or s.text.startswith(("1", "2")) for s in slides), "no template text"
    assert slides[2].text.startswith("Pipeline model")
    assert Chapter("Week 3", chapter_pdfs).ingest()[0].sha == pdfs[0].sha  # cache hit


def test_figure_detection(chapter_pdfs):
    slides = {s.id: s for s in build_slides(Chapter("Week 3", chapter_pdfs).ingest())}
    # the logo on every slide is template, not a figure
    assert all(not s.figures for sid, s in slides.items() if sid not in ("A·3", "B·3", "B·5"))
    raster = slides["A·3"].figures
    assert [(f.id, f.source) for f in raster] == [("f1", "raster")]
    vector = slides["B·3"].figures
    assert len(vector) == 1 and vector[0].source == "vector", "shape-drawn diagram must be cropped"
    x0, y0, x1, y1 = vector[0].bbox
    assert x0 <= 345 and x1 >= 595 and y0 <= 110 and y1 >= 250  # covers the whole tree
    assert slides["B·3"].figure_path("f1").exists()
    # the diagram pasted again on B·5 is recognised as the same figure as A·3's
    assert slides["B·5"].canonical == {"f1": "A-003-f1"}
    assert slides["B·5"].figure_asset("f1") == "A-003-f1"
    assert raster[0].digests and raster[0].digests == slides["B·5"].figures[0].digests


def test_recap_slide_marked_duplicate(chapter_pdfs):
    slides = build_slides(Chapter("Week 3", chapter_pdfs).ingest())
    dups = {s.id: s.duplicate_of for s in slides if s.duplicate_of}
    assert dups == {"B·1": "A·2"}


# ---------- outline ----------

def test_repair_assigns_every_slide_once():
    order = ["A·1", "A·2", "A·3", "A·4", "B·2"]
    extracts = {sid: _extract(sid) for sid in order}
    extracts["A·1"] = _extract("title", role="title")
    outline = Outline(chapter_title="t", overview="o", filler=["A·9"], sections=[
        Section(title="Intro", slides=["A·1"], summary="", notes=""),  # title slide: forced to filler
        Section(title="One", slides=["A·2", "A·2", "Z·1"], summary="", notes=""),
        Section(title="Two", slides=["B·2"], summary="", notes=""),
        Section(title="Empty", slides=[], summary="", notes=""),
    ])
    fixed = repair(outline, order, {"title"}, extracts)
    placed = [s for sec in fixed.sections for s in sec.slides] + fixed.filler
    assert sorted(placed) == sorted(order)
    assert fixed.filler == ["A·1"]
    assert fixed.sections[0].slides == ["A·2", "A·3", "A·4"]
    assert [s.title for s in fixed.sections] == ["One", "Two"]


def test_continuation_slides_merge_into_previous_section():
    order = ["A·1", "A·2", "A·3", "A·4", "A·5", "A·6"]
    titles = ["Recursion tree", "Recursion tree (cont.)", "Master theorem", "Examples continued",
              "TLB", "TLB:"]  # same title on consecutive slides = one topic
    extracts = {sid: _extract(t) for sid, t in zip(order, titles)}
    outline = Outline(chapter_title="t", overview="o", filler=[], sections=[
        Section(title="Tree", slides=["A·1"], summary="", notes=""),
        Section(title="Tree 2", slides=["A·2"], summary="", notes=""),
        Section(title="Master", slides=["A·3"], summary="", notes=""),
        Section(title="More", slides=["A·4"], summary="", notes=""),
        Section(title="TLB", slides=["A·5"], summary="", notes=""),
        Section(title="TLB again", slides=["A·6"], summary="", notes=""),
    ])
    fixed = repair(outline, order, set(), extracts)
    assert [(s.title, s.slides) for s in fixed.sections] == [
        ("Tree", ["A·1", "A·2"]), ("Master", ["A·3", "A·4"]), ("TLB", ["A·5", "A·6"])]


# ---------- figures ----------

def test_figure_assets():
    ex = _extract("x", figures=[_fig("essential", "f2"), _fig("decorative", "f1"),
                                _fig("helpful", None), _fig("essential", "f2", "same crop again")])
    assert [a for a, _ in figure_assets(_StubSlide("B·7"), ex)] == ["assets/B-007-f2.png", "assets/B-007.png"]
    shared = _StubSlide("B·7", {"f2": "A-001-f1"})  # same diagram first shown on A·1
    assert figure_assets(shared, ex)[0][0] == "assets/A-001-f1.png"


def test_ensure_essential_figures():
    extracts = {"A·3": _extract("x", figures=[_fig("essential", "f1"), _fig("helpful", "f2")])}
    slides = {"A·3": _StubSlide("A·3")}
    md = ensure_essential_figures("## S\ntext", ["A·3"], extracts, slides)
    assert "](assets/A-003-f1.png)" in md and "A-003-f2" not in md  # only essential ones forced
    already = "## S\n![d](assets/A-003-f1.png)"
    assert ensure_essential_figures(already, ["A·3"], extracts, slides) == already
    # shown in an earlier section -> not forced again
    assert ensure_essential_figures("## S", ["A·3"], extracts, slides, {"assets/A-003-f1.png"}) == "## S"


def test_drop_repeat_images():
    md = "![a](assets/A-001-f1.png)\ntext\n![b](assets/A-001-f1.png)\n![c](assets/A-002.png)\n"
    assert drop_repeat_images(md, set()) == "![a](assets/A-001-f1.png)\ntext\n![c](assets/A-002.png)\n"
    assert drop_repeat_images(md, {"assets/A-002.png"}).count("A-002") == 0


def test_resolve_asset(chapter_pdfs):
    slides = build_slides(Chapter("Week 3", chapter_pdfs).ingest())
    by_fid = {slide_file_id(s.id): s for s in slides}
    assert resolve_asset("A-003", by_fid) == by_fid["A-003"].image_path
    assert resolve_asset("A-003-f1", by_fid).name == "p003-f1.png"
    assert resolve_asset("A-003-f9", by_fid) is None
    assert resolve_asset("Z-001", by_fid) is None


# ---------- markdown hygiene ----------

def test_compress_ids():
    assert compress_ids(["A·4", "A·5", "A·6", "B·2", "B·4"]) == "A·4-6, B·2, B·4"


def test_check_markdown():
    assert check_markdown("Energy $E = mc^2$ and $$\\frac{a}{b}$$") == []
    assert "unbalanced $ inline math" in check_markdown("cost is $5 per item")
    assert check_markdown("```python\nx = '$'\n```") == []
    assert any("braces" in p for p in check_markdown("$\\frac{a}{b$"))
    assert check_markdown("```\nopen fence") == ["unclosed code fence"]


def test_normalize_section():
    assert normalize_section("# Big\ntext", "T") == "## Big\ntext"
    assert normalize_section("## A\n# Stray\nx", "T") == "## A\n### Stray\nx"
    assert normalize_section("intro text", "T").startswith("## T\n")
    assert normalize_section("```\n# comment\n```", "T").endswith("# comment\n```")
    fixed = normalize_section("## A\n<details><summary>Formal</summary>\n$$x$$\n</details>", "T")
    assert fixed == "## A\n<details><summary>Formal</summary>\n\n$$x$$\n\n</details>"
    ok = "## A\n<details><summary>F</summary>\n\n$$x$$\n\n</details>"
    assert normalize_section(ok, "T") == ok


# ---------- end to end ----------

class FakeLLM:
    """Deterministic stand-in for a model backend (multimodal, so the writer gets images)."""
    model_id = "fake"
    generation_id = "fake"
    max_concurrency = 4
    cfg = {"vision": True}

    def __init__(self):
        self.writer_images = 0
        self.writer_inputs: list[str] = []

    def connect(self):
        pass

    def structured(self, messages, schema, **kw):
        from backend.pipeline.chapterize import ChapterSplit, ChapterStart
        from backend.pipeline.per_deck import DeckOverlap, SectionOverlap
        if schema is ChapterSplit:  # two chapters: part1 basics, part2 from the master theorem on
            return ChapterSplit(chapters=[ChapterStart(title="Basics", first_slide="A·1"),
                                          ChapterStart(title="Recurrences", first_slide="B·2")])
        if schema is DeckOverlap:  # every later deck's first section re-explains deck 1's first one
            return DeckOverlap(overlaps=[SectionOverlap(section=1, earlier=[1], relation="same",
                                                        new_in_this_section="a worked example")])
        if schema is SlideExtract:
            parts = messages[1]["content"]
            hint = parts[-1]["text"]
            title = hint.split("<<<\n")[1].splitlines()[0]
            crops = re.findall(r"Cropped region (f\d+)", " ".join(p.get("text", "") for p in parts))
            role = ("title" if "CS F211" in title else
                    "filler" if title in ("Questions?", "Thank you") else "content")
            figs = [_fig("essential", crops[0], f"diagram on {title}")] if crops else []
            return _extract(title, role, figs)
        ids = re.findall(r"^### ([A-Z]·\d+)", messages[1]["content"], flags=re.M)
        return Outline(chapter_title="Algorithms Week 3", overview="Complexity basics.", filler=[],
                       sections=[Section(title="Complexity", slides=ids[1:4], summary="", notes=""),
                                 # "Questions?"/"Thank you" deliberately misplaced: repair() must fix it
                                 Section(title="Recurrences", slides=ids[4:], summary="", notes="")])

    def chat(self, messages, **kw):
        content = messages[1]["content"]
        if isinstance(content, list):
            self.writer_images += sum(p["type"] == "image_url" for p in content)
            content = content[0]["text"]
        if content.startswith("Word limit:"):  # summary of one learner section
            heading = re.search(r"^## .*$", content, flags=re.M).group(0)
            return f"{heading}\n\n- **Key point:** condensed fact.\n- Another fact."
        if "key takeaways" in messages[0]["content"]:  # chapter takeaways
            return "Here you go:\n- **Complexity:** grows with n.\n- **Recurrences:** solved by the master theorem."
        if content.startswith("SOURCE:"):  # fact-check pass: return the draft, lightly edited
            self.reviews = getattr(self, "reviews", 0) + 1
            return content.split("DRAFT:\n", 1)[1] + "\n\n> **Beyond the slides:** checked."
        self.writer_inputs.append(content)
        title = re.search(r"Section to write: (.*)", content).group(1)
        # never embeds the real figures (assemble must add essential ones) and invents a bad path
        covered = "; ".join(re.findall(r'"title":"([^"]*)"', content))  # a real writer covers its slides
        return (f"## {title}\n\nInline $O(n)$ math, explained in enough words to look like a real "
                f"section body. Covers: {covered}.\n\n![made up](assets/Z-999.png)")


def test_end_to_end_with_fake_llm(chapter_pdfs, monkeypatch):
    import backend.pipeline.run as run

    fake = FakeLLM()
    monkeypatch.setattr(run, "stage_llm", lambda stage, backend=None: fake)
    chapter = Chapter("Week 3", chapter_pdfs)
    out = run.convert_chapter(chapter, ["learner", "donkey"], by_topic=True)

    doc = out["learner"].read_text()
    assert doc.startswith("# Algorithms Week 3")
    assert "<!-- slides A·2-4 -->" in doc and "<!-- slides B·2-5 -->" in doc
    assert "B·1" not in doc and "B·6" not in doc and "A·5" not in doc  # duplicate + filler
    assert "- [Complexity](#complexity)" in doc
    # both the raster and the shape-drawn diagram reach the doc, as crops, with files copied
    for asset in ("A-003-f1", "B-003-f1"):
        assert f"](assets/{asset}.png)" in doc
        assert (chapter.dir / "assets" / f"{asset}.png").exists()
    assert "Z-999" not in doc, "invented image paths are dropped"
    assert doc.count("](assets/A-003-f1.png)") == 1, "diagram repeated on B·5 is embedded once"
    # per level: section 1 sees A-003-f1; section 2 sees B-003-f1 + A-003-f1 (B·5 repeats it)
    assert fake.writer_images == 6
    assert out["donkey"].exists()
    assert fake.reviews == 4 and "Beyond the slides:** checked" in doc  # 2 sections x 2 levels reviewed
    manifest = json.loads((chapter.dir / "chapter.json").read_text())
    assert [p["letter"] for p in manifest["pdfs"]] == ["A", "B"]
    assert set(manifest["levels"]) == {"learner-by-topic", "donkey-by-topic"}


# ---------- robustness ----------

class FlakyLLM(FakeLLM):
    """Fails extraction for one slide and every write of one section."""

    def structured(self, messages, schema, **kw):
        if schema is SlideExtract and "Linear search" in messages[1]["content"][-1]["text"]:
            raise ValueError("malformed JSON from model")
        return super().structured(messages, schema, **kw)

    def chat(self, messages, **kw):
        content = messages[1]["content"]
        text = content[0]["text"] if isinstance(content, list) else content
        if "Section to write: Recurrences" in text:
            raise TimeoutError("model timed out")
        return super().chat(messages, **kw)


def test_failures_degrade_gracefully(chapter_pdfs, monkeypatch, tmp_path):
    import backend.pipeline.run as run

    flaky = FlakyLLM()
    monkeypatch.setattr(run, "stage_llm", lambda stage, backend=None: flaky)
    monkeypatch.setattr(FlakyLLM, "model_id", "flaky")  # separate extract cache
    monkeypatch.setattr(FlakyLLM, "generation_id", "flaky")
    chapter = Chapter("Flaky week", chapter_pdfs)
    doc = run.convert_chapter(chapter, ["proficient"], by_topic=True)["proficient"].read_text()
    # failed slide fell back to its text layer, failed section to the raw slide content
    assert any("def find(xs, t):" in t for t in flaky.writer_inputs)
    assert "could not be rewritten automatically" in doc
    assert "## Complexity" in doc  # the healthy section is still model-written
    from backend.pipeline.ingest import file_sha
    sha = file_sha(chapter_pdfs[0])
    failed = list(chapter_pdfs[0].parent.parent.glob(f"cache/{sha}/extract/flaky/*/004.json"))
    assert not failed, "fallback extracts are not cached"


# ---------- regressions seen on the real Paging deck ----------

def test_details_pulled_out_of_blockquote():
    md = ("## S\n> **Question 1:** How many PTEs?\n\n> <details>\n> <summary>Answer</summary>\n\n>\n"
          "> $$\n> 2^{20}\n> $$\n> </details>\n\nafter")
    out = normalize_section(md, "S")
    assert "> **Question 1:** How many PTEs?" in out          # the question keeps its callout
    assert "\n<details>\n<summary>Answer</summary>\n\n" in out  # details no longer quoted
    assert "\n$$\n2^{20}\n$$\n" in out and "\n\n</details>" in out
    assert out.endswith("after")


def test_fenced_table_is_unfenced():
    md = "## S\n```\n| PFN | valid |\n|-----|-------|\n| 10  |   1   |\n```\n```\nnot | a table\n```"
    out = normalize_section(md, "S")
    assert "| PFN | valid |\n|-----|-------|\n| 10  |   1   |" in out
    assert "```\n| PFN" not in out
    assert "```\nnot | a table\n```" in out  # real code blocks untouched


def test_auto_figure_goes_before_closing_quiz():
    extracts = {"A·3": _extract("x", figures=[_fig("essential", "f1")])}
    md = "## S\nbody\n\n#### Check your understanding\n1. Q?"
    out = ensure_essential_figures(md, ["A·3"], extracts, {"A·3": _StubSlide("A·3")})
    assert out.index("assets/A-003-f1.png") < out.index("Check your understanding")


def test_inline_math_and_callout_lists():
    md = ("## S\nPages: $ 2^{32} / 2^{12} = 2^{20} $ and $x$ and $$ y $$\n"
          "```\nprice = '$ 5 $'\n```\n> **Key takeaways:**\n- one\n- two\n\nafter")
    out = normalize_section(md, "S")
    assert "Pages: $2^{32} / 2^{12} = 2^{20}$ and $x$ and $$ y $$" in out
    assert "price = '$ 5 $'" in out                     # code untouched
    assert "> **Key takeaways:**\n> - one\n> - two\n\nafter" in out


def test_extract_tolerates_omitted_empty_lists():
    ex = SlideExtract.model_validate_json('{"slide_role": "content", "title": "T", "bullets": ["a"]}')
    assert ex.tables == [] and ex.figures == [] and ex.code == []


def test_outline_accepts_id_objects():
    o = Outline.model_validate_json(
        '{"chapter_title": "t", "sections": [{"title": "S", "slides": [{"slide_id": "A·2"}, "A·3"]}],'
        ' "filler": [{"slide_id": "A·1", "reason": "Course Title Slide"}]}')
    assert o.filler == ["A·1"] and o.sections[0].slides == ["A·2", "A·3"]


def test_fragmented_outline_is_capped_and_titles_cleaned():
    from backend.pipeline.outline import target_sections
    order = [f"A·{i}" for i in range(1, 21)]
    extracts = {sid: _extract(f"topic {sid}") for sid in order}
    outline = Outline(chapter_title="t", sections=[
        Section(title=f"{i}. Topic {i}", slides=[sid]) for i, sid in enumerate(order, 1)])
    target = target_sections(20)
    fixed = repair(outline, order, set(), extracts, target)
    assert target == 5 and len(fixed.sections) <= 8
    assert [s for sec in fixed.sections for s in sec.slides] == order   # order + coverage preserved
    assert not any(re.match(r"\d", sec.title) for sec in fixed.sections)


def test_inline_math_tightening_pairs_delimiters_in_order():
    from backend.pipeline.assemble import tighten_inline_math
    line = "Directory A has $ 2^{27} $ entries. It spans $2^{18}$ physical pages and $$ x $$ too."
    assert tighten_inline_math(line) == \
        "Directory A has $2^{27}$ entries. It spans $2^{18}$ physical pages and $$ x $$ too."
    assert tighten_inline_math("costs $5 per page") == "costs $5 per page"  # lone $: untouched


class RamblingLLM(FakeLLM):
    """First draft of each section degenerates (thinking out loud + word salad); retries are clean."""
    generation_id = "rambling"

    def chat(self, messages, **kw):
        content = messages[-1]["content"]
        text = content[0]["text"] if isinstance(content, list) else content
        rejected = "previous attempt" in messages[0]["content"]
        if text.startswith("Chapter:") and not rejected:
            title = re.search(r"Section to write: (.*)", text).group(1)
            salad = " ".join(["musicians actors dancers singers composers conductors"] * 15)
            return f"## {title}\n\nOffset 96? No, let's look closer. Wait, let's re-check.\n\n{salad}"
        return super().chat(messages, **kw)


def test_degenerate_drafts_are_regenerated(chapter_pdfs, monkeypatch):
    import backend.pipeline.run as run
    from backend.pipeline.assemble import degeneration

    rambling = RamblingLLM()
    monkeypatch.setattr(run, "stage_llm", lambda stage, backend=None: rambling)
    doc = run.convert_chapter(Chapter("Rambling", chapter_pdfs), ["learner"], by_topic=True)["learner"].read_text()
    assert "musicians" not in doc and "Wait, let" not in doc
    assert degeneration(doc) is None


def test_degeneration_detector():
    from backend.pipeline.assemble import degeneration, strip_degenerate
    clean = "## S\n\nA page table maps pages to frames. Each entry has a valid bit.\n\n```\nx x x x x x x x\n```"
    assert degeneration(clean) is None
    salad = "## S\n\nGood intro.\n\n" + " ".join(f"word{i}" for i in range(80))
    assert "without punctuation" in degeneration(salad)
    assert "thinks out loud" in degeneration("## S\n\nIt maps to 96? No, let's look. Wait, let's re-check.")
    assert degeneration(strip_degenerate(salad)) is None and "Good intro." in strip_degenerate(salad)


def test_large_sections_split_and_merges_capped():
    from backend.pipeline.outline import MAX_SLIDES_PER_SECTION, split_large_sections
    order = [f"A·{i}" for i in range(1, 21)]
    big = Outline(chapter_title="t", sections=[Section(title="Huge", slides=order[:10]),
                                               Section(title="Rest", slides=order[10:])])
    out = split_large_sections(big)
    assert [len(x.slides) for x in out.sections] == [5, 5, 5, 5]
    assert out.sections[0].title == "Huge (part 1)" and out.sections[1].title == "Huge (part 2)"
    # the fragmented outline from the capping test: no merged section exceeds the cap
    extracts = {sid: _extract(sid) for sid in order}
    frag = Outline(chapter_title="t", sections=[Section(title=f"T{i}", slides=[sid]) for i, sid in enumerate(order)])
    fixed = repair(frag, order, set(), extracts, 3)
    assert max(len(x.slides) for x in fixed.sections) <= MAX_SLIDES_PER_SECTION


def test_slide_references_and_long_alt_text_cleaned():
    md = ("## S\n\n**Slide A·104 Analysis**\n\nText.\n\n### DWARF Info (Slides A·106–A·108)\n\n"
          "![" + "A very long description. " * 10 + "](assets/A-104.png)\n\n```\n# Slide A·1 in code stays\n```")
    out = normalize_section(md, "S")
    assert "Slide A·104" not in out and "### DWARF Info\n" in out
    assert "![A very long description](assets/A-104.png)" in out
    assert "# Slide A·1 in code stays" in out


def test_summary_output(chapter_pdfs, monkeypatch):
    import backend.pipeline.run as run

    fake = FakeLLM()
    monkeypatch.setattr(run, "stage_llm", lambda stage, backend=None: fake)
    chapter = Chapter("Summary week", chapter_pdfs)
    out = run.convert_chapter(chapter, ["summary"], by_topic=True)
    assert set(out) == {"summary"} and not (chapter.dir / "learner.md").exists()
    doc = out["summary"].read_text()
    assert "> **Level:** Summary" in doc
    assert "> - **Complexity:** grows with n." in doc and "Here you go" not in doc  # takeaways, bullets only
    assert doc.count("- **Key point:** condensed fact.") == 2                     # one per section
    assert "- [Complexity](#complexity)" in doc


def test_strip_degenerate_drops_repeated_bullets_and_inline_slide_refs():
    from backend.pipeline.assemble import strip_degenerate
    md = "## S\n\n- **`x`**: form A.\n- **`y`**: form B.\n- **`x`**: form A.\n\n```\n.x\n.x\n```"
    out = strip_degenerate(md)
    assert out.count("**`x`**: form A.") == 1 and out.count(".x") == 2  # code untouched
    assert normalize_section("## S\nLook at the table in Slide A·109. Done.", "S") == \
        "## S\nLook at the table. Done."


# ---------- per-slide mode ----------

def _stub_slides(ids):
    from types import SimpleNamespace
    pdf = SimpleNamespace(sha="x")
    out = []
    for k, sid in enumerate(ids, 1):
        st = _StubSlide(sid)
        st.pdf, st.duplicate_of, st.page = pdf, None, k
        out.append(st)
    return out


def test_per_slide_outline_handles_overlap():
    from backend.pipeline.per_slide import build_per_slide_outline
    ids = ["A·1", "A·2", "A·3", "A·4", "A·5"]
    slides = _stub_slides(ids)
    slides[4].duplicate_of = "A·2"                      # exact repeat of slide 2
    ex = {
        "A·1": _extract("Course intro", role="title"),
        "A·2": _extract("Paging", figures=[_fig("essential", "f1")]),
        "A·3": _extract("Paging (cont.)"),
        "A·4": _extract("TLB"),
    }
    ex["A·2"].bullets = ["Memory is split into fixed size pages", "Page tables map pages to frames"]
    ex["A·3"].bullets = ["Memory is split into fixed-size pages.", "Each process has its own page table"]
    ex["A·4"].bullets = ["The TLB caches recent translations"]
    slides[2].canonical = {"f1": "A-002-f1"}             # A·3 shows A·2's diagram again
    ex["A·3"].figures = [_fig("essential", "f1")]
    o = build_per_slide_outline("Ch", "ov", slides, ex)

    assert o.mode == "slides" and len(o.sections) == 5   # n slides -> n sections
    assert [s.kind for s in o.sections] == ["filler", "content", "content", "content", "duplicate"]
    assert o.sections[1].title == "Slide 2 · Paging"
    s3 = o.sections[2]
    assert "fixed-size pages" in s3.context and "[Slide 2](#slide-2--paging)" in s3.context
    assert "figure `assets/A-002-f1.png`" in s3.context and s3.overlaps == ["A·2"]
    assert "Each process" not in s3.context              # new content is not marked as repeated
    assert s3.budget_scale < 1 and o.sections[3].budget_scale == 1 and not o.sections[3].context
    assert "repeats [Slide 2](#slide-2--paging)" in o.sections[4].context


def test_per_slide_end_to_end(chapter_pdfs, monkeypatch):
    import backend.pipeline.run as run

    fake = FakeLLM()
    monkeypatch.setattr(run, "stage_llm", lambda stage, backend=None: fake)
    chapter = Chapter("Per slide week", chapter_pdfs)
    out = run.convert_chapter(chapter, ["learner", "summary"], per_slide=True)
    doc = out["learner"].read_text()
    assert out["learner"].name == "learner-by-slide.md" and out["summary"].name == "summary-by-slide.md"
    headings = re.findall(r"^## (Slide \S+) ·", doc, flags=re.M)
    assert headings == ["Slide A1", "Slide A2", "Slide A3", "Slide A4", "Slide A5",
                        "Slide B1", "Slide B2", "Slide B3", "Slide B4", "Slide B5", "Slide B6"]  # 11 slides -> 11
    assert "repeats [Slide A2]" in doc                     # B·1 is the recap duplicate of A·2
    assert doc.count("](assets/A-003-f1.png)") == 1        # B·5 re-shows A·3's diagram: linked, not embedded
    assert "No new teaching content" in doc                # title / thank-you slides
    assert not (chapter.dir / "learner.md").exists()       # topic-mode docs untouched


def test_per_deck_end_to_end(chapter_pdfs, monkeypatch):
    import backend.pipeline.run as run

    fake = FakeLLM()
    monkeypatch.setattr(run, "stage_llm", lambda stage, backend=None: fake)
    chapter = Chapter("Deck week", chapter_pdfs)
    out = run.convert_chapter(chapter, ["learner", "summary"], per_deck=True)
    doc = out["learner"].read_text()
    assert out["learner"].name == "learner-by-deck.md" and out["summary"].name == "summary-by-deck.md"

    parts = re.findall(r"^## (Deck \d+ · .+)$", doc, flags=re.M)
    assert [p.split(" · ")[0] for p in parts] == ["Deck 1", "Deck 2"]     # 2 PDFs -> 2 parts, in order
    assert "*Source: part1.pdf*" in doc and "*Source: part2.pdf*" in doc
    assert doc.count("<!-- slides ") >= 2                                  # each deck written from its own slides
    deck2 = doc[doc.index("\n## " + parts[1]):]
    assert "recap slide(s) here repeat material covered in [Deck 1" in deck2   # B·1 = A·2
    assert doc.count("](assets/A-003-f1.png)") == 1                       # B·5 re-shows deck 1's diagram
    assert "<!-- slides A·" not in deck2                                   # no topic mixes decks
    assert (chapter.dir / ".cache" / "outline-decks-A.json").exists() and (chapter.dir / ".cache" / "outline-decks-B.json").exists()
    recap = [t for t in fake.writer_inputs if "This topic was already explained in 'Complexity' in [Deck 1" in t]
    assert recap and "a worked example" in recap[0]           # semantic overlap reached the writer
    assert (chapter.dir / ".cache" / "overlap-decks-B.json").exists() and not (chapter.dir / ".cache" / "overlap-decks-A.json").exists()


def test_per_deck_pure_repeat_becomes_recap(chapter_pdfs, monkeypatch):
    import backend.pipeline.run as run
    from backend.pipeline.per_deck import DeckOverlap, SectionOverlap

    class RepeatLLM(FakeLLM):
        def structured(self, messages, schema, **kw):
            if schema is DeckOverlap:
                return DeckOverlap(overlaps=[SectionOverlap(section=1, earlier=[1], relation="same",
                                                            new_in_this_section="nothing new")])
            return super().structured(messages, schema, **kw)

    fake = RepeatLLM()
    monkeypatch.setattr(run, "stage_llm", lambda stage, backend=None: fake)
    doc = run.convert_chapter(Chapter("Recap week", chapter_pdfs), ["learner"], per_deck=True)["learner"].read_text()
    deck2 = doc[doc.index("\n## Deck 2"):]
    assert "This revisits material explained in 'Complexity' in [Deck 1" in deck2
    assert "The slides recap these points" in deck2
    # deck 2's first section was not sent to the writer
    assert not any("Section to write: Complexity" in t and "B·" in t for t in fake.writer_inputs)


def test_repeat_image_becomes_link():
    md = "## S\n![d](assets/A-001-f1.png)\ntext"
    out = drop_repeat_images(md, {"assets/A-001-f1.png": "Deck 1 · Paging"})
    assert out == "## S\n*(Same diagram as shown earlier in [Deck 1 · Paging](#deck-1--paging).)*\ntext"


def test_tighten_accepts_shorter_rejects_lossy():
    from backend.pipeline.write import tighten

    class Stub:
        def __init__(self, reply): self.reply = reply
        def chat(self, *a, **k): return self.reply

    long_md = "## S\n\n" + "Paging splits memory into pages. " * 8 + "\n\n![d](assets/A-001-f1.png)"
    short = "## S\n\nPaging splits memory into fixed-size pages, mapped by page tables.\n\n![d](assets/A-001-f1.png)"
    assert tighten(long_md, 50, Stub(short), 500) == short
    assert tighten(long_md, 50, Stub(short.replace("![d](assets/A-001-f1.png)", "")), 500) == long_md  # lost figure
    assert tighten(long_md, 50, Stub("Sure! Here it is"), 500) == long_md                              # not a section



# ---------- chapter-wise layout (default) ----------

def test_chapter_normalise_contiguous_and_no_tiny_chapters():
    from backend.pipeline.chapterize import ChapterStart, normalise
    ids = [f"A·{i}" for i in range(1, 31)]
    slides = _stub_slides(ids)
    ex = {sid: _extract(sid) for sid in ids}
    starts = [ChapterStart(title="B", first_slide="A·12"), ChapterStart(title="Tiny", first_slide="A·20"),
              ChapterStart(title="C", first_slide="A·23"), ChapterStart(title="Bogus", first_slide="Z·9")]
    chapters = normalise(starts, slides, ex)
    # the first chapter is extended back to A·1, "Tiny" (3 slides) merges into it (keeping its subject
    # in the title), Z·9 is ignored
    assert [(t, g[0].id, g[-1].id) for t, g in chapters] == [("B and Tiny", "A·1", "A·22"), ("C", "A·23", "A·30")]
    assert sum(len(g) for _, g in chapters) == 30


def test_small_upload_is_one_chapter_without_a_call(chapter_pdfs):
    from backend.pipeline.chapterize import detect_chapters
    slides = build_slides(Chapter("Week 3", chapter_pdfs).ingest())
    ex = {s.id: _extract("t") for s in slides if not s.duplicate_of}

    class NoCall:
        def structured(self, *a, **k): raise AssertionError("no detection call expected")

    chapters = detect_chapters(Chapter("Week 3", chapter_pdfs), slides, ex, NoCall())
    assert len(chapters) == 1 and len(chapters[0][1]) == len(slides)


def test_chapter_wise_end_to_end(chapter_pdfs, monkeypatch):
    import backend.pipeline.chapterize as chz
    import backend.pipeline.run as run

    monkeypatch.setattr(chz, "SINGLE_TOPIC_MAX", 2)      # force detection on the tiny fixture
    monkeypatch.setattr(chz, "MIN_CHAPTER_SLIDES", 1)
    fake = FakeLLM()
    monkeypatch.setattr(run, "stage_llm", lambda stage, backend=None: fake)
    chapter = Chapter("Chapters week", chapter_pdfs)
    out = run.convert_chapter(chapter, ["learner", "summary"])           # default layout
    doc = out["learner"].read_text()
    assert out["learner"].name == "learner.md" and out["summary"].name == "summary.md"
    assert re.findall(r"^## (Chapter \d+ · .+)$", doc, flags=re.M) == ["Chapter 1 · Basics", "Chapter 2 · Recurrences"]
    toc = doc[doc.index("## Contents"):doc.index("\n---")]
    assert toc.count("- [") == 2 and "  - [" not in toc                   # contents list: chapters only
    ch1 = doc[doc.index("## Chapter 1"):doc.index("## Chapter 2")]
    assert "<!-- slides B·" not in ch1                                     # no part-2 topic inside chapter 1
    assert any("This section is the whole of 'Chapter 1 · Basics'" in t for t in fake.writer_inputs)
    assert "\n---\n\n<!-- slides" not in doc                              # no separators inside chapters



def test_chapter_starts_snap_to_title_slides_and_same_titles_merge():
    from backend.pipeline.chapterize import ChapterStart, normalise
    ids = [f"A·{i}" for i in range(1, 31)]
    slides = _stub_slides(ids)
    ex = {sid: _extract(sid) for sid in ids}
    ex["A·10"] = _extract("Lecture 5: Paging", role="title")
    ex["A·11"] = _extract("Agenda", role="agenda")
    starts = [ChapterStart(title="Intro", first_slide="A·1"), ChapterStart(title="Paging", first_slide="A·12"),
              ChapterStart(title="paging!", first_slide="A·20")]
    chapters = normalise(starts, slides, ex)
    assert [(t, g[0].id, g[-1].id) for t, g in chapters] == [("Intro", "A·1", "A·9"), ("Paging", "A·10", "A·30")]



def test_mid_chapter_closing_blocks_are_stripped():
    from backend.pipeline.assemble import strip_closing_blocks
    md = ("## Part\n\nBody text.\n\n#### Check your understanding\n1. Q?\n<details><summary>Answer</summary>\n\nA.\n\n"
          "</details>\n\n### Next topic\n\nMore.\n\n> **Key takeaways:**\n> - one\n> - two")
    out = strip_closing_blocks(md)
    assert out == "## Part\n\nBody text.\n\n### Next topic\n\nMore."



def test_missing_slides_detected():
    from backend.pipeline.assemble import missing_slides
    ex = {"A·1": _extract("Ambiguity in grammars"), "A·2": _extract("Closure of CFLs under union")}
    ex["A·1"].key_terms = ["ambiguous grammar", "parse tree"]
    ex["A·2"].key_terms = ["union", "concatenation", "Kleene star"]
    md = "## S\nAn ambiguous grammar has two parse trees for one string."
    assert missing_slides(md, ["A·1", "A·2"], ex) == ["A·2"]
    assert missing_slides(md + " CFLs are closed under union, concatenation and Kleene star.", ["A·1", "A·2"], ex) == []



def test_json_reply_with_raw_newlines_and_prose_is_accepted():
    from backend.llm.client import parse_json_reply
    raw = '{"slide_role": "content", "title": "Lab", "tables": ["| a | b |\n|---|---|\n| 1 | 2 |"]}'
    raw = raw.replace("\\n", "\n")              # literal control characters inside the string
    assert "\n" in raw
    ex = parse_json_reply(raw, SlideExtract)
    assert ex.tables[0].count("|") == 9
    ex = parse_json_reply("Here is the JSON:\n" + raw + "\nDone.", SlideExtract)
    assert ex.title == "Lab"



def test_json_reply_with_unterminated_string_is_repaired():
    from backend.llm.client import parse_json_reply
    raw = ('{"slide_role": "content", "title": "Lab Schedule", "tables": [\n'
           '    "| Date | Topic |\\n|---|---|\\n| Nov 24 | Holiday |\n  ],\n  "key_terms": ["IPC"]\n}')
    ex = parse_json_reply(raw, SlideExtract)
    assert ex.title == "Lab Schedule" and "Nov 24" in ex.tables[0] and ex.key_terms == ["IPC"]



def test_figure_never_inserted_above_a_summary_titled_heading():
    from backend.pipeline.assemble import insert_before_closing
    md = "## Summary of CPU Scheduling\n\nBody.\n\n#### Check your understanding\n1. Q?"
    out = insert_before_closing(md, "![f](assets/A-001-f1.png)")
    assert out.startswith("## Summary of CPU Scheduling")
    assert out.index("assets/A-001-f1") < out.index("Check your understanding")
    assert insert_before_closing("## Summary\n\nBody.", "X").startswith("## Summary")


# ---------- topic layout (one flat book across every PDF) ----------

def test_topic_target_scales_with_slides_beyond_a_chapters_ceiling():
    from backend.pipeline.outline import target_sections
    assert target_sections(250, per_section=4) == 12                      # chapter default: capped at 12
    assert target_sections(250, per_section=5, minimum=2, maximum=80) == 50
    assert target_sections(3, per_section=5, minimum=2, maximum=80) == 2


def test_fallback_outline_groups_runs_without_crossing_pdfs():
    from backend.pipeline.outline import fallback_outline
    ids = [f"A·{i}" for i in range(1, 8)] + [f"B·{i}" for i in range(1, 4)]
    extracts = {sid: _extract(f"Topic {sid}", "content", []) for sid in ids}
    extracts["A·1"] = _extract("Course intro", "title", [])
    out = fallback_outline("Book", ids, extracts, per_section=3)
    assert [s.slides for s in out.sections] == [["A·2", "A·3", "A·4"], ["A·5", "A·6", "A·7"],
                                                ["B·1", "B·2", "B·3"]]
    assert out.filler == ["A·1"] and out.sections[0].title == "Topic A·2"


def test_topic_layout_survives_a_failed_outline_call(chapter_pdfs, monkeypatch, capsys):
    import backend.pipeline.run as run
    from backend.llm.client import LLMError
    from backend.pipeline.outline import Outline

    class OutlineDown(FakeLLM):
        def structured(self, messages, schema, **kw):
            if schema is Outline:
                raise LLMError("model down")
            return super().structured(messages, schema, **kw)

    monkeypatch.setattr(run, "stage_llm", lambda stage, backend=None: OutlineDown())
    chapter = Chapter("Book fallback", chapter_pdfs)
    doc = run.convert_chapter(chapter, ["learner"], by_topic=True)["learner"].read_text()
    assert "outline failed" in capsys.readouterr().out
    assert doc.count("<!-- slides ") >= 2          # still a multi-section book, named after each run's first slide
    assert "Chapter 1" not in doc and "- [Recap" in doc


def test_spend_cap_is_not_swallowed_by_the_outline_fallback(chapter_pdfs, monkeypatch):
    import backend.pipeline.run as run
    from backend.llm.usage import BudgetExceeded
    from backend.pipeline.outline import Outline

    class CapHit(FakeLLM):
        def structured(self, messages, schema, **kw):
            if schema is Outline:
                raise BudgetExceeded("cap")
            return super().structured(messages, schema, **kw)

    monkeypatch.setattr(run, "stage_llm", lambda stage, backend=None: CapHit())
    with pytest.raises(BudgetExceeded):
        run.convert_chapter(Chapter("Capped", chapter_pdfs), ["learner"], by_topic=True)


def test_summary_keeps_the_learner_sections_title():
    from backend.pipeline.summarize import keep_heading
    learner = "## Paging Fundamentals: Motivation, Structure, and Translation\n\nLong notes."
    assert keep_heading("## Paging Fundamentals\n\n- point", learner) == (
        "## Paging Fundamentals: Motivation, Structure, and Translation\n\n- point")
    assert keep_heading("- point without a heading", learner).startswith("## Paging Fundamentals: Motivation")
    assert keep_heading("## Short\n\n- point", "No heading here") == "## Short\n\n- point"   # nothing to keep
    assert keep_heading("### Deeper\n\n- p", "### Part title\n\nx") == "### Part title\n\n- p"


def test_empty_readings_are_rejected_not_cached():
    from backend.pipeline.extract import is_empty_reading

    class S:  # just enough of a Slide: its text layer
        def __init__(self, text):
            self.text = text

    wordy = S("Page table is array of page table entries, one per page of process and more")
    blank = _extract("Page Table Entry (invented title)", "content").model_copy(update={"bullets": []})
    assert is_empty_reading(blank, wordy)                                  # valid JSON, nothing in it, text on the slide
    assert not is_empty_reading(blank, S("Figure only"))                   # almost no text: a figure slide may be empty
    assert not is_empty_reading(blank.model_copy(update={"slide_role": "title"}), wordy)   # title slides are sparse
    assert not is_empty_reading(_extract("Paging", "content"), wordy)       # has a bullet


def test_code_slides_are_not_transcribed_twice():
    from backend.pipeline.extract import CodeBlock, drop_code_duplicates

    listing = CodeBlock(language="assembly", code=".byte 0\n.section .debug_aranges,\"\",\n  @progbits\n.long 0x2c")
    bullets = [".byte 0", '.section .debug_aranges,"", @progbits', ".long 0x2c", "Debug sections describe address ranges"]
    kept, code = drop_code_duplicates(bullets, [listing])
    assert kept == ["Debug sections describe address ranges"] and code == [listing]   # echoes gone, prose kept
    # short bullets and bullets that merely share words with the code are never dropped
    assert drop_code_duplicates(["ret", "uses .byte"], [listing])[0] == ["ret", "uses .byte"]
    # lone inline identifiers tagged as code blocks are dropped when the slide has prose bullets...
    word = CodeBlock(language="lisp", code="eval")
    assert drop_code_duplicates(["Evaluators use eval and apply"], [word])[1] == []
    # ...but a real one-liner, or a code-only slide, keeps its block
    assert drop_code_duplicates(["x"], [CodeBlock(language="sh", code="ls -l")])[1][0].code == "ls -l"
    assert drop_code_duplicates([], [word])[1] == [word]


def test_notes_level_is_condensed_from_learner_and_caches_live_in_hidden_dir(chapter_pdfs, monkeypatch):
    import backend.pipeline.run as run

    fake = FakeLLM()
    monkeypatch.setattr(run, "stage_llm", lambda stage, backend=None: fake)
    chapter = Chapter("Notes week", chapter_pdfs)
    out = run.convert_chapter(chapter, ["notes"], by_topic=True)
    assert set(out) == {"notes"} and not (chapter.dir / "learner.md").exists()
    doc = out["notes"].read_text()
    assert "> **Level:** Notes" in doc and "condensed fact" in doc
    assert "Key takeaways" not in doc                       # notes carry no chapter takeaways
    assert (chapter.dir / ".cache" / "sections" / "notes").is_dir()
    assert not [p for p in chapter.dir.iterdir() if p.name.startswith(("sections", "outline"))]  # nothing loose


def test_old_cache_layout_is_moved_and_still_hits(chapter_pdfs, monkeypatch):
    import backend.pipeline.run as run

    fake = FakeLLM()
    monkeypatch.setattr(run, "stage_llm", lambda stage, backend=None: fake)
    chapter = Chapter("Migrate week", chapter_pdfs)
    run.convert_chapter(chapter, ["learner"], by_topic=True)
    for name in ("sections", "outline-sections.json"):       # put the caches back where old versions kept them
        src = chapter.dir / ".cache" / name
        if src.exists():
            src.rename(chapter.dir / name)
    writes_before = len(fake.writer_inputs)
    run.convert_chapter(chapter, ["learner"], by_topic=True)
    assert len(fake.writer_inputs) == writes_before          # moved back into .cache/, every section a hit
    assert not (chapter.dir / "sections").exists() and (chapter.dir / ".cache" / "sections").is_dir()


def test_dockey_is_still_accepted_as_donkey():
    from backend.cli import parse_levels

    assert parse_levels("dockey,learner") == ["donkey", "learner"]
