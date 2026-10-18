"""Past-year papers: reading, merging, mapping to topics, the report and the "Asked in" tags (offline)."""

import json
import re

import pymupdf as fitz
import pytest

import backend.pipeline.run as run
from backend.pipeline.chapter import Chapter
from backend.pipeline.papers import (Analysis, Paper, PaperPage, PaperQuestion, PaperSolution, Question, analyse,
                                     is_solution_file, merge_pages, norm_number, pair_solutions)
from test_pipeline import FakeLLM

PAGES = {   # page marker in the text layer -> what the "model" reads off that page
    "M23-P1": PaperPage(exam="Mid Semester", year="2023", questions=[
        PaperQuestion(number="1", marks=5, text="Define big-O notation formally."),
        PaperQuestion(number="2", marks=8, text="Solve the recurrence T(n)=2T(n/2)+n using the master theorem and", continues=True)]),
    "M23-P2": PaperPage(questions=[
        PaperQuestion(number="", text="state the asymptotic bound."),
        PaperQuestion(number="3", marks=4, text="Differentiate best case and worst case running time.")]),
    "M23-SOL": PaperPage(role="solutions", solutions=[PaperSolution(number="Q1", text="f(n)=O(g(n)) iff ..."),
                                                    PaperSolution(number="2", text="Theta(n log n)")]),
    "C22-P1": PaperPage(exam="Comprehensive", year="2022", questions=[
        PaperQuestion(number="1", marks=5, text="Define the big-O notation formally."),
        PaperQuestion(number="2", marks=6, text="Explain quantum teleportation with a circuit.")]),
}


class PaperFake(FakeLLM):
    def __init__(self):
        super().__init__()
        self.page_calls = 0
        self.map_calls = 0

    def structured(self, messages, schema, **kw):
        from backend.pipeline.papers import PaperMap, QuestionMap
        if schema is PaperPage:
            self.page_calls += 1
            hint = messages[1]["content"][-1]["text"]
            return PAGES[re.search(r"[A-Z]\d\d-[A-Z0-9]+", hint).group(0)]
        if schema is PaperMap:
            self.map_calls += 1
            out = []
            for qid, text in re.findall(r"^(P\d+\.\S+?)(?: \[[^\]]*\])?: (.*)$", messages[1]["content"], flags=re.M):
                if "recurrence" in text:
                    out.append(QuestionMap(q=qid, sections=[2, 9], coverage="on-slides", type="numerical", concept="master theorem"))
                elif "big-O" in text:
                    out.append(QuestionMap(q=qid, sections=[1], coverage="on-slides", type="definition", concept="big-O"))
                elif "best case" in text:
                    out.append(QuestionMap(q=qid, sections=[1], coverage="on-slides", type="compare", concept="cases"))
                else:
                    out.append(QuestionMap(q=qid, sections=[], coverage="not-on-slides", type="explain", concept="quantum"))
            return PaperMap(questions=out)
        return super().structured(messages, schema, **kw)


@pytest.fixture
def paper_files(tmp_path):
    files = []
    for name, marker in [("midsem_2023.pdf", ["M23-P1", "M23-P2"]), ("midsem_2023_solutions.pdf", ["M23-SOL"]),
                         ("compre_2022.pdf", ["C22-P1"])]:
        doc = fitz.open()
        for m in marker:
            page = doc.new_page(width=400, height=500)
            page.insert_text((40, 60), f"{m} exam page", fontsize=14)
        path = tmp_path / name
        doc.save(path)
        files.append(path)
    return files


def test_file_pairing_and_numbers():
    from pathlib import Path
    assert is_solution_file(Path("Midsem 2023 Solutions.pdf")) and not is_solution_file(Path("midsem_2023.pdf"))
    qs, att = pair_solutions([Path("compre_2022.pdf"), Path("midsem_2023.pdf"), Path("midsem_2023_soln.pdf")])
    assert [p.name for p in qs] == ["compre_2022.pdf", "midsem_2023.pdf"]
    assert [p.name for p in att[Path("midsem_2023.pdf")]] == ["midsem_2023_soln.pdf"] and not att[Path("compre_2022.pdf")]
    assert norm_number("Q2 (b)") == norm_number("2(b)") == "2b"


def test_question_split_across_pages_is_joined():
    qs, sols, exam, year = merge_pages([PAGES["M23-P1"], PAGES["M23-P2"], PAGES["M23-SOL"]])
    assert [q.number for q in qs] == ["1", "2", "3"]
    assert qs[1].text.endswith("master theorem and\nstate the asymptotic bound.") and qs[1].marks == 8
    assert sols == {"1": "f(n)=O(g(n)) iff ...", "2": "Theta(n log n)"} and (exam, year) == ("Mid Semester", "2023")


def test_papers_end_to_end(chapter_pdfs, paper_files, monkeypatch):
    fake = PaperFake()
    monkeypatch.setattr(run, "stage_llm", lambda stage, backend=None: fake)
    chapter = Chapter("Papers week", chapter_pdfs, papers=paper_files)
    out = run.convert_chapter(chapter, ["learner", "summary"], by_topic=True)

    assert out["papers"] == chapter.dir / "papers.md"
    report = out["papers"].read_text()
    assert "2 paper(s), 5 questions, 28 marks" in report            # 5+8+4 and 5+6
    assert "| Mid Semester 2023 | midsem_2023.pdf | 3 | 17 | yes |" in report   # solutions file paired in
    assert "| Comprehensive 2022 | compre_2022.pdf | 2 | 11 | no |" in report
    # ranking: Recurrences (8 m) vs Complexity (5+4+5 = 14 m), the bad section number 9 was dropped
    assert report.index("Complexity") < report.index("Recurrences")
    assert "Mid Semester 2023 Q1 (5 m); Comprehensive 2022 Q1 (5 m)" in report
    assert "Near-identical questions across papers" in report and "Mid Semester 2023 Q1" in report
    assert "Not covered by the slides" in report and "Comprehensive 2022 Q2 (6 m)" in report
    assert "<summary>Official solution</summary>" in report and "Theta(n log n)" in report
    assert (chapter.dir / "papers.json").exists() and json.loads((chapter.dir / "papers.json").read_text())["papers"]

    for level in ("learner", "summary"):                             # tags land in every level
        doc = out[level].read_text()
        assert "> **Asked in:** Mid Semester 2023 Q1 (5 m) · Mid Semester 2023 Q3 (4 m) · Comprehensive 2022 Q1 (5 m)" in doc
        assert "> **Asked in:** Mid Semester 2023 Q2 (8 m)" in doc
    # a second run reuses every reading, mapping and section
    pages, maps, writes = fake.page_calls, fake.map_calls, len(fake.writer_inputs)
    run.convert_chapter(chapter, ["learner"], by_topic=True)
    assert (fake.page_calls, fake.map_calls, len(fake.writer_inputs)) == (pages, maps, writes)


def test_high_yield_sections_are_starred():
    def q(i, paper, sec, marks):
        return Question(id=f"{paper}.{i}", paper=paper, number=str(i), marks=marks, text=f"question {i} about topic",
                        sections=[sec], coverage="on-slides", type="explain")
    papers = [Paper(id="P1", label="Midsem 2023", questions=[q(1, "P1", 0, 10), q(2, "P1", 1, 5), q(3, "P1", 2, 3), q(4, "P1", 3, 2)])]
    a: Analysis = analyse(papers)
    assert a.stars == {0}                                            # top quarter of 4 asked sections
    assert a.tags[0] == ["Midsem 2023 Q1 (10 m)"]
