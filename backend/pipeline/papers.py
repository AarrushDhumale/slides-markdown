"""Past-year papers: read them, map every question to the course's topics, and report what is asked.

Papers (typed or scanned PDFs, optionally with official solutions) are read page by page with the vision
reader, merged into questions, and mapped to the outline's sections with one cached text call per paper.
The outputs are `papers.json` + `papers.md` (topic ranking, question patterns, repeats, gaps) and, for the
docs themselves, an "Asked in" line under each section plus a ★ on the high-yield ones.
No model writes solutions: an official solution is shown if the paper came with one.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, Field, field_validator

from backend.llm.client import LLM, image_part
from backend.llm.usage import BudgetExceeded
from backend.paths import PROMPTS_DIR
from backend.pipeline.chapter import cache_root
from backend.pipeline.extract import SlideExtract, model_slug
from backend.pipeline.ingest import IngestedPdf, ingest_pdf
from backend.pipeline.outline import Outline

PAPER_VERSION = "v1"                # bump when the schema or prompt changes, to invalidate cached readings
MAP_CHUNK = 25                      # questions per mapping call
QUESTION_TYPES = ("definition", "explain", "compare", "derive-prove", "numerical", "trace", "code", "design",
                  "mcq", "other")
STAR_FRACTION = 0.25                # the top quarter of asked sections (by marks) are "high-yield"
DUPLICATE_SIMILARITY = 0.6          # token-set overlap above which two questions count as the same question
SOLUTION_FILE_RE = re.compile(r"solution|soln|answer|\bkeys?\b|\bsols?\b|marking|scheme", re.I)


# ---------- reading ----------

class PaperQuestion(BaseModel):
    number: str = ""
    marks: float | None = None
    text: str = ""
    figure: str = ""
    continues: bool = False

    @field_validator("number", mode="before")
    @classmethod
    def _number(cls, v):
        return "" if v is None else str(v).strip()

    @field_validator("marks", mode="before")
    @classmethod
    def _marks(cls, v):
        if v is None or isinstance(v, (int, float)):
            return v
        m = re.search(r"\d+(?:\.\d+)?", str(v))
        return float(m.group(0)) if m else None


class PaperSolution(BaseModel):
    number: str = ""
    text: str = ""

    @field_validator("number", mode="before")
    @classmethod
    def _number(cls, v):
        return "" if v is None else str(v).strip()


class PaperPage(BaseModel):
    role: Literal["cover", "questions", "solutions"] = "questions"
    exam: str = ""
    year: str = ""
    questions: list[PaperQuestion] = Field(default_factory=list)
    solutions: list[PaperSolution] = Field(default_factory=list)

    @field_validator("exam", "year", mode="before")
    @classmethod
    def _text(cls, v):
        return "" if v is None else str(v).strip()


def is_solution_file(path: Path) -> bool:
    return bool(SOLUTION_FILE_RE.search(path.stem))


def _page_cache(pdf: IngestedPdf, page: int, llm: LLM) -> Path:
    return pdf.dir / "paper" / model_slug(llm.model_id) / PAPER_VERSION / f"{page:03d}.json"


def read_page(pdf: IngestedPdf, page, llm: LLM, system_prompt: str) -> PaperPage:
    path = _page_cache(pdf, page.page, llm)
    if path.exists():
        return PaperPage.model_validate_json(path.read_text())
    hint = page.text or "(no text layer: read everything from the image)"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": [
            {"type": "text", "text": f"Exam paper page {page.page}:"},
            image_part(pdf.dir / page.image),
            {"type": "text", "text": f"Text layer (may be garbled or missing; the image is authoritative):\n<<<\n{hint}\n>>>"},
        ]},
    ]
    result = llm.structured(messages, PaperPage, max_tokens=6144)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(indent=1))
    return result


def read_pdf(pdf: IngestedPdf, llm: LLM, system_prompt: str) -> list[PaperPage]:
    """Every page read concurrently, in page order. A page that cannot be read raises (and is not cached)."""
    with ThreadPoolExecutor(max_workers=llm.max_concurrency) as pool:
        return list(pool.map(lambda p: read_page(pdf, p, llm, system_prompt), pdf.pages))


# ---------- merging pages into papers ----------

class Question(BaseModel):
    id: str                          # unique within the chapter, e.g. "P1.2b"
    paper: str                       # paper id, "P1"
    number: str
    marks: float | None = None
    text: str
    figure: str = ""
    solution: str = ""
    sections: list[int] = Field(default_factory=list)   # outline section indices (0-based)
    coverage: Literal["on-slides", "partly", "not-on-slides", "unknown"] = "unknown"
    type: str = "other"
    concept: str = ""


class Paper(BaseModel):
    id: str
    label: str                       # "Mid-Semester Test 2023"
    exam: str = ""
    year: str = ""
    source: str = ""
    has_solutions: bool = False
    questions: list[Question] = Field(default_factory=list)

    @property
    def total_marks(self) -> float:
        return sum(q.marks or 0 for q in self.questions)


def norm_number(n: str) -> str:
    """'Q2 (b)' / '2(b)' / '2.b' -> '2b'; the key solutions are matched on."""
    n = re.sub(r"^(?:question|ques|q)(?=[\s.\d(])\.?\s*", "", n.strip(), flags=re.I)
    return re.sub(r"[^0-9a-z]", "", n.lower())


def merge_pages(pages: list[PaperPage]) -> tuple[list[PaperQuestion], dict[str, str], str, str]:
    """(questions with continuations joined, {normalised number: solution}, exam, year) for one PDF."""
    questions: list[PaperQuestion] = []
    solutions: dict[str, str] = {}
    exam = year = ""
    for pg in pages:
        exam, year = exam or pg.exam, year or pg.year
        for q in pg.questions:
            prev = questions[-1] if questions else None
            same = prev is not None and prev.continues and (not q.number or norm_number(q.number) == norm_number(prev.number))
            if same:   # the tail of a question that began on the previous page
                prev.text = f"{prev.text.rstrip()}\n{q.text.lstrip()}".strip()
                prev.figure = " ".join(x for x in (prev.figure, q.figure) if x)
                prev.marks = prev.marks if prev.marks is not None else q.marks
                prev.continues = q.continues
            elif q.text.strip():
                questions.append(q.model_copy())
        for s in pg.solutions:
            key = norm_number(s.number)
            if key and s.text.strip():
                solutions[key] = f"{solutions[key]}\n\n{s.text.strip()}" if key in solutions else s.text.strip()
    return questions, solutions, exam, year


def year_from(text: str) -> str:
    m = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", text)
    return m.group(1) if m else ""


def _name_key(path: Path) -> str:
    return re.sub(r"[^a-z0-9]+", " ", SOLUTION_FILE_RE.sub(" ", path.stem.lower())).strip()


def pair_solutions(papers: list[Path]) -> tuple[list[Path], dict[Path, list[Path]]]:
    """Split the files into question papers and solution files, and attach each solution file to the paper
    whose name (and year) matches best. A lone paper takes every solution file."""
    questions = [p for p in papers if not is_solution_file(p)]
    attached: dict[Path, list[Path]] = {p: [] for p in questions}
    for sol in (p for p in papers if is_solution_file(p)):
        if not questions:
            questions.append(sol)   # only solution files given: treat each as its own paper
            attached[sol] = []
            continue
        sol_year = year_from(sol.stem)

        def score(p: Path) -> float:
            s = SequenceMatcher(None, _name_key(sol), _name_key(p)).ratio()
            return s + (0.5 if sol_year and sol_year == year_from(p.stem) else 0)

        attached[max(questions, key=score)].append(sol)
    return questions, attached


def build_papers(files: list[Path], llm: LLM) -> list[Paper]:
    """Read every file and return the papers (questions, official solutions attached by question number)."""
    system_prompt = (PROMPTS_DIR / "paper.md").read_text()
    questions_files, attached = pair_solutions(files)
    ingested = {f: ingest_pdf(f) for f in {*questions_files, *(s for v in attached.values() for s in v)}}
    if any(not _page_cache(pdf, p.page, llm).exists() for pdf in ingested.values() for p in pdf.pages):
        llm.connect()
    papers: list[Paper] = []
    for n, f in enumerate(questions_files, start=1):
        pages = read_pdf(ingested[f], llm, system_prompt)
        qs, solutions, exam, year = merge_pages(pages)
        for sol_file in attached[f]:
            _, extra, e2, y2 = merge_pages(read_pdf(ingested[sol_file], llm, system_prompt))
            solutions = {**extra, **solutions}
            exam, year = exam or e2, year or y2
        year = year or year_from(f.stem)
        label = " ".join(x for x in (exam, year) if x) or f.stem
        pid = f"P{n}"
        out, seen = [], Counter()
        for q in qs:
            key = norm_number(q.number) or str(len(out) + 1)
            seen[key] += 1
            qid = f"{pid}.{key}" + (f"-{seen[key]}" if seen[key] > 1 else "")
            out.append(Question(id=qid, paper=pid, number=q.number or key, marks=q.marks, text=q.text.strip(),
                                figure=q.figure.strip(), solution=solutions.get(key, "")))
        if out:
            papers.append(Paper(id=pid, label=label, exam=exam, year=year, source=f.name,
                                has_solutions=any(q.solution for q in out), questions=out))
        else:
            print(f"\n  ! {f.name}: no questions found; skipped")
    return papers


# ---------- mapping questions to topics ----------

class QuestionMap(BaseModel):
    q: str
    sections: list[int] = Field(default_factory=list, description="1-based numbers of the topic sections")
    coverage: Literal["on-slides", "partly", "not-on-slides"] = "on-slides"
    type: str = "other"
    concept: str = ""

    @field_validator("sections", mode="before")
    @classmethod
    def _sections(cls, v):
        if v is None:
            return []
        if not isinstance(v, list):
            v = [v]
        out = []
        for x in v:
            m = re.search(r"\d+", str(x))
            if m:
                out.append(int(m.group(0)))
        return out


class PaperMap(BaseModel):
    questions: list[QuestionMap] = Field(default_factory=list)


def section_digest(i: int, sec, extracts: dict[str, SlideExtract]) -> str:
    terms = sorted({t for sid in sec.slides if sid in extracts for t in extracts[sid].key_terms})[:12]
    pts = [b.strip()[:100] for sid in sec.slides if sid in extracts for b in extracts[sid].bullets][:5]
    head = f"{i}. " + (f"[{sec.group}] " if sec.group else "") + sec.title
    return (head + (f" — {sec.summary}" if sec.summary else "")
            + "".join(f"\n    · {p}" for p in pts) + (f"\n    key terms: {', '.join(terms)}" if terms else ""))


def question_block(q: Question) -> str:
    marks = f" [{q.marks:g} marks]" if q.marks is not None else ""
    fig = f"\n   (figure: {q.figure})" if q.figure else ""
    return f"{q.id}{marks}: {q.text[:900]}{fig}"


def map_paper(paper: Paper, outline: Outline, extracts: dict[str, SlideExtract], llm: LLM, chapter) -> None:
    """Fill sections / coverage / type / concept on the paper's questions (cached per paper)."""
    prompt = (PROMPTS_DIR / "paper_map.md").read_text()
    content = [(i, s) for i, s in enumerate(outline.sections) if s.kind == "content"]
    listing = "\n".join(section_digest(n, s, extracts) for n, (_, s) in enumerate(content, start=1))
    index = {n: i for n, (i, _) in enumerate(content, start=1)}   # prompt number -> outline index

    for start in range(0, len(paper.questions), MAP_CHUNK):
        chunk = paper.questions[start:start + MAP_CHUNK]
        user = ("TOPIC SECTIONS:\n" + listing + "\n\nEXAM QUESTIONS:\n" + "\n\n".join(question_block(q) for q in chunk))
        key = hashlib.sha256(f"{prompt}\n{user}\n{llm.generation_id}".encode()).hexdigest()[:16]
        path = cache_root(chapter) / f"papers-map-{paper.id}-{start // MAP_CHUNK}.json"
        result: PaperMap | None = None
        if path.exists():
            cached = json.loads(path.read_text())
            if cached.get("key") == key:
                result = PaperMap.model_validate(cached["result"])
        if result is None:
            llm.connect()
            result = llm.structured([{"role": "system", "content": prompt}, {"role": "user", "content": user}],
                                    PaperMap, max_tokens=4096)
            path.write_text(json.dumps({"key": key, "result": result.model_dump()}, indent=1, ensure_ascii=False))
        by_id = {m.q: m for m in result.questions}
        for q in chunk:
            m = by_id.get(q.id)
            if not m:
                continue    # left "unknown" / unmapped
            q.sections = list(dict.fromkeys(index[n] for n in m.sections if n in index))   # drops bad numbers
            q.coverage = m.coverage if (q.sections or m.coverage == "not-on-slides") else "not-on-slides"
            q.type = m.type if m.type in QUESTION_TYPES else "other"
            q.concept = m.concept.strip()


# ---------- analysis ----------

def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2}


def near_duplicates(papers: list[Paper]) -> list[tuple[Question, Question]]:
    """Questions from different papers that ask (nearly) the same thing, by word-set overlap."""
    pool = [(q, _tokens(q.text)) for p in papers for q in p.questions]
    out = []
    for i, (a, ta) in enumerate(pool):
        for b, tb in pool[i + 1:]:
            if a.paper == b.paper or len(ta) < 4 or len(tb) < 4:
                continue
            if len(ta & tb) / len(ta | tb) >= DUPLICATE_SIMILARITY:
                out.append((a, b))
    return out


@dataclass
class SectionStat:
    index: int
    questions: list[Question] = field(default_factory=list)
    marks: float = 0.0          # a question's marks are split evenly over the sections it maps to

    def years(self, papers: dict[str, Paper]) -> list[str]:
        return sorted({papers[q.paper].label for q in self.questions})


@dataclass
class Analysis:
    papers: list[Paper]
    stats: dict[int, SectionStat]
    unmapped: list[Question]
    gaps: list[Question]
    duplicates: list[tuple[Question, Question]]
    total_marks: float
    stars: set[int]
    tags: dict[int, list[str]]


def analyse(papers: list[Paper]) -> Analysis:
    by_paper = {p.id: p for p in papers}
    stats: dict[int, SectionStat] = {}
    unmapped, gaps = [], []
    for p in papers:
        for q in p.questions:
            if q.coverage == "not-on-slides":
                gaps.append(q)
            if not q.sections:
                unmapped.append(q)
                continue
            share = (q.marks or 0) / len(q.sections)
            for i in q.sections:
                st = stats.setdefault(i, SectionStat(i))
                st.questions.append(q)
                st.marks += share
    ranked = sorted(stats.values(), key=lambda s: (-s.marks, -len(s.questions), s.index))
    n_stars = math.ceil(STAR_FRACTION * len(ranked)) if len(ranked) > 3 else 0
    stars = {s.index for s in ranked[:n_stars]}
    tags: dict[int, list[str]] = {}
    for i, st in stats.items():
        tags[i] = [f"{by_paper[q.paper].label} Q{q.number}" + (f" ({q.marks:g} m)" if q.marks is not None else "")
                   for q in sorted(st.questions, key=lambda q: (q.paper, q.id))]
    return Analysis(papers, stats, unmapped, gaps, near_duplicates(papers),
                    sum(p.total_marks for p in papers), stars, tags)


def exam_tag_line(tags: list[str], star: bool) -> str:
    return ("> **★ High-yield** · " if star else "> ") + "**Asked in:** " + " · ".join(tags)


# ---------- report ----------

def _link(outline: Outline, i: int, doc: str) -> str:
    from backend.pipeline.assemble import anchor
    title = outline.sections[i].title
    return f"[{title}]({doc}#{anchor(title)})"


def _qline(q: Question, papers: dict[str, Paper]) -> str:
    return f"{papers[q.paper].label} Q{q.number}" + (f" ({q.marks:g} m)" if q.marks is not None else "")


def render_report(a: Analysis, outline: Outline, doc: str) -> str:
    papers = {p.id: p for p in a.papers}
    n_q = sum(len(p.questions) for p in a.papers)
    out = [f"# Past-paper analysis: {outline.chapter_title}", "",
           f"{len(a.papers)} paper(s), {n_q} questions, {a.total_marks:g} marks. "
           f"Topic links point into [{doc}]({doc}). Marks of a question that maps to several topics are split between them.", "",
           "## Papers", "", "| Paper | File | Questions | Marks | Official solutions |", "|---|---|---|---|---|"]
    for p in a.papers:
        out.append(f"| {p.label} | {p.source} | {len(p.questions)} | {p.total_marks:g} | {'yes' if p.has_solutions else 'no'} |")

    ranked = sorted(a.stats.values(), key=lambda s: (-s.marks, -len(s.questions), s.index))
    out += ["", "## Topics ranked by marks", "",
            "| # | Topic | Marks | % of marks | Times asked | Where asked | Question types |", "|---|---|---|---|---|---|---|"]
    for n, st in enumerate(ranked, start=1):
        types = ", ".join(f"{t} ×{c}" for t, c in Counter(q.type for q in st.questions).most_common())
        pct = f"{100 * st.marks / a.total_marks:.0f}%" if a.total_marks else "–"
        star = "★ " if st.index in a.stars else ""
        out.append(f"| {n} | {star}{_link(outline, st.index, doc)} | {st.marks:g} | {pct} | {len(st.questions)} | "
                   f"{'; '.join(_qline(q, papers) for q in st.questions)} | {types} |")
    never = [(i, s) for i, s in enumerate(outline.sections) if s.kind == "content" and i not in a.stats]
    if never:
        out += ["", "**Never asked in these papers:** " + "; ".join(_link(outline, i, doc) for i, _ in never)]

    groups: dict[str, list[SectionStat]] = defaultdict(list)
    for st in a.stats.values():
        groups[outline.sections[st.index].group].append(st)
    if len([g for g in groups if g]) > 1:
        out += ["", "## Chapters ranked by marks", "", "| Chapter | Marks | % of marks | Times asked |", "|---|---|---|---|"]
        for g, sts in sorted(groups.items(), key=lambda kv: -sum(s.marks for s in kv[1])):
            m = sum(s.marks for s in sts)
            out.append(f"| {g or '–'} | {m:g} | {f'{100 * m / a.total_marks:.0f}%' if a.total_marks else '–'} | "
                       f"{len({q.id for s in sts for q in s.questions})} |")

    out += ["", "## Question patterns", ""]
    overall = Counter(q.type for p in a.papers for q in p.questions)
    out.append("Overall: " + ", ".join(f"{t} ×{c}" for t, c in overall.most_common()) + ".")
    patterns = [(st, t, [q for q in st.questions if q.type == t])
                for st in ranked for t in {q.type for q in st.questions}]
    repeats = [(st, t, qs) for st, t, qs in patterns if len({q.paper for q in qs}) >= 2]
    if repeats:
        out += ["", "**Same kind of question on the same topic in more than one paper:**", ""]
        for st, t, qs in sorted(repeats, key=lambda r: (-len(r[2]), r[0].index)):
            out.append(f"- {_link(outline, st.index, doc)}: *{t}*: " + "; ".join(_qline(q, papers) for q in qs))
    if a.duplicates:
        out += ["", "**Near-identical questions across papers:**", ""]
        for x, y in a.duplicates:
            out.append(f"- {_qline(x, papers)} ≈ {_qline(y, papers)}: {x.concept or x.text[:80].strip()}")
    if not repeats and not a.duplicates:
        out.append("\nNo question type or question repeats across the papers given.")

    out += ["", "## Not covered by the slides", ""]
    if a.gaps:
        out += [f"- {_qline(q, papers)}: {q.concept or q.text[:100].strip()}" for q in a.gaps]
    else:
        out.append("Every question maps to topics in the notes.")
    loose = [q for q in a.unmapped if q not in a.gaps]
    if loose:
        out += ["", "**Unmapped:** " + "; ".join(_qline(q, papers) for q in loose)]

    out += ["", "## Questions by paper"]
    for p in a.papers:
        out += ["", f"### {p.label}", ""]
        for q in p.questions:
            topics = ", ".join(_link(outline, i, doc) for i in q.sections) or "unmapped"
            marks = f" ({q.marks:g} marks)" if q.marks is not None else ""
            out += [f"**Q{q.number}**{marks} · *{q.type}* · {topics}", "", q.text.strip()]
            if q.figure:
                out += ["", f"*Figure: {q.figure}*"]
            if q.solution:
                out += ["", "<details><summary>Official solution</summary>", "", q.solution, "", "</details>"]
            out.append("")
    return "\n".join(out).rstrip() + "\n"


# ---------- entry point ----------

def analyse_papers(chapter, files: list[Path], outline: Outline, extracts: dict[str, SlideExtract],
                   read_llm: LLM, map_llm: LLM, doc_name: str = "learner.md",
                   progress: Callable[[int, int], None] | None = None) -> Analysis | None:
    """Read and map the papers and write papers.json + papers.md into the chapter dir. A paper that fails
    (other than by a spend cap) is skipped with a warning; None if nothing could be analysed."""
    try:
        papers = build_papers(files, read_llm)
    except BudgetExceeded:
        raise
    except Exception as e:
        print(f"\n  ! reading the past papers failed ({type(e).__name__}: {str(e)[:150]}); continuing without them")
        return None
    mapped: list[Paper] = []
    for n, p in enumerate(papers, start=1):
        try:
            map_paper(p, outline, extracts, map_llm, chapter)
            mapped.append(p)
        except BudgetExceeded:
            raise
        except Exception as e:
            print(f"\n  ! mapping {p.label} to the topics failed ({type(e).__name__}: {str(e)[:150]}); skipped")
        if progress:
            progress(n, len(papers))
    if not mapped:
        return None
    a = analyse(mapped)
    chapter.dir.mkdir(parents=True, exist_ok=True)
    (chapter.dir / "papers.json").write_text(json.dumps(
        {"papers": [p.model_dump() for p in mapped], "stars": sorted(a.stars),
         "sections": [{"index": i, "title": s.title, "group": s.group} for i, s in enumerate(outline.sections)]},
        indent=1, ensure_ascii=False))
    (chapter.dir / "papers.md").write_text(render_report(a, outline, doc_name))
    return a
