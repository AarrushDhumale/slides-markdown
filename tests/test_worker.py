"""Web worker, "Explain this" context and quiz extraction — offline (in-memory Supabase, fake LLM)."""

import json
import uuid

import pytest

from backend import worker as worker_mod
from backend.pipeline.explain import (ExplainError, ExplainRequest, build_messages, expand_ids, replace_section,
                                       split_sections)
from backend.pipeline.quiz import extract_quiz
from test_pipeline import FakeLLM


class FakeSupabase:
    def __init__(self):
        self.tables: dict[str, list[dict]] = {"jobs": [], "notebooks": [], "sources": [], "documents": [],
                                              "explain_requests": [], "explain_cache": [], "worker_status": [],
                                              "chapters": [], "chapter_docs": [], "section_choices": [],
                                              "section_variants": []}
        self.storage: dict[tuple[str, str], bytes] = {}
        self.log: list[str] = []      # order of chapter publications

    def insert(self, table, row):
        self.tables[table].append({"id": str(uuid.uuid4()), "status": "new", **row})

    def rpc(self, fn, params=None):
        if fn == "claim_chapter":
            ch = next(r for r in self.tables["chapters"] if r["id"] == params["p_chapter"])
            if ch["status"] == "generating" and ch.get("generating_job") != params["p_job"]:
                return False
            ch.update(status="generating", generating_job=params["p_job"])
            return True
        table, status = {"claim_job": ("jobs", "queued"), "claim_explain": ("explain_requests", "queued")}[fn]
        for row in self.tables[table]:
            if row["status"] == status:
                row["status"] = "running"
                return [dict(row)]
        return []

    def select(self, table, query):
        filters = dict(p.split("=", 1) for p in query.split("&") if "=eq." in p)
        return [r for r in self.tables[table]
                if all(str(r.get(k)) == v.removeprefix("eq.") for k, v in filters.items())]

    def update(self, table, match, data):
        for r in self.tables[table]:
            if all(r.get(k) == v for k, v in match.items()):
                r.update(data)

    def upsert(self, table, rows, on_conflict):
        keys = on_conflict.split(",")
        for row in rows if isinstance(rows, list) else [rows]:
            existing = next((r for r in self.tables[table] if all(r.get(k) == row[k] for k in keys)), None)
            if existing:
                existing.update(row)
            else:
                self.tables[table].append(dict(row))

    def download(self, bucket, path):
        return self.storage[(bucket, path)]

    def upload(self, bucket, path, data, content_type):
        self.storage[(bucket, path)] = data
        if path.endswith("/learner.md"):
            self.log.append(path.split("/")[0])


@pytest.fixture
def web(tmp_path, monkeypatch, chapter_pdfs):
    import backend.pipeline.run as run

    fake_llm = FakeLLM()
    monkeypatch.setattr(run, "stage_llm", lambda stage, backend=None: fake_llm)
    monkeypatch.setattr(worker_mod, "WEB_DIR", tmp_path / "web" / "notebooks")
    monkeypatch.setattr(worker_mod, "LIBRARY_DIR", tmp_path / "web" / "library")
    db = FakeSupabase()
    nb = str(uuid.uuid4())
    db.tables["notebooks"].append({"id": nb, "title": "Week 3", "status": "processing"})
    for i, pdf in enumerate(chapter_pdfs):
        db.storage[("pdfs", f"{nb}/{i}.pdf")] = pdf.read_bytes()
        db.tables["sources"].append({"id": str(i), "notebook_id": nb, "filename": pdf.name, "position": i,
                                     "storage_path": f"{nb}/{i}.pdf", "size_bytes": pdf.stat().st_size})
    return db, nb


def run_jobs(db, *jobs):
    for i, (nb, levels) in enumerate(jobs):
        db.tables["jobs"].append({"id": f"job-{len(db.tables['jobs'])}", "notebook_id": nb, "levels": levels,
                                  "status": "queued"})
    w = worker_mod.Worker(db, worker_id="test")
    w.once = True
    w.job_loop()
    return w


def test_job_end_to_end(web):
    db, nb = web
    run_jobs(db, (nb, ["summary"]))

    job = db.tables["jobs"][0]
    assert job["status"] == "done", job.get("error")
    assert db.tables["notebooks"][0]["status"] == "ready"
    # one library chapter per PDF, each published to its own folder
    assert len(db.tables["chapters"]) == 2 and job["chapters_done"] == job["chapters_total"] == 2
    assert all(c["status"] == "ready" for c in db.tables["chapters"])
    assert all(len(c["sha"]) == 16 and c["slug"].endswith(c["sha"][:8]) for c in db.tables["chapters"])
    first = db.tables["chapters"][0]
    files = {path for bucket, path in db.storage if bucket == "library"}
    for name in ("summary.md", "learner.md", "quiz.json", "slides.json", "extracts.json"):
        assert f"{first['slug']}/{name}" in files, name
    assert any(f.startswith(f"{first['slug']}/assets/") for f in files)
    assert any(f.startswith(f"{first['slug']}/slides/A-") for f in files)
    slides = json.loads(db.storage[("library", f"{first['slug']}/slides.json")])
    assert slides["A·1"]["image"].startswith("slides/A-")
    for c in db.tables["chapters"]:
        levels = {d["level"] for d in db.tables["chapter_docs"] if d["chapter_id"] == c["id"]}
        assert levels == {"summary", "learner", "quiz"}
    assert all(s["chapter_id"] for s in db.tables["sources"])
    # local work stays under the library dir, never data/chapters
    assert (worker_mod.LIBRARY_DIR / first["slug"] / "summary-by-topic.md").exists()
    # each chapter is a book of topics: several slide anchors, no chapter headings
    book = db.storage[("library", f"{first['slug']}/learner.md")].decode()
    assert book.count("<!-- slides ") >= 1 and "## Chapter" not in book
    assert first["title"] == book.splitlines()[0][2:].strip()


def test_chapters_publish_one_by_one(web):
    db, nb = web
    run_jobs(db, (nb, ["learner"]))
    slugs = [c["slug"] for c in db.tables["chapters"]]
    assert db.log == slugs      # chapter 1 was uploaded before chapter 2 (in source order)


def test_same_pdf_in_another_notebook_is_not_regenerated(web, monkeypatch):
    db, nb = web
    run_jobs(db, (nb, ["learner", "summary"]))
    docs = list(db.tables["chapter_docs"])
    spend = []
    import backend.pipeline.run as run
    monkeypatch.setattr(run, "convert_chapter", lambda *a, **k: spend.append(1))
    nb2 = str(uuid.uuid4())
    db.tables["notebooks"].append({"id": nb2, "title": "Copy", "status": "processing"})
    for i, src in enumerate(list(db.tables["sources"])):
        db.storage[("pdfs", f"{nb2}/{i}.pdf")] = db.storage[("pdfs", src["storage_path"])]
        db.tables["sources"].append({"id": f"c{i}", "notebook_id": nb2, "filename": src["filename"], "position": i,
                                     "storage_path": f"{nb2}/{i}.pdf", "size_bytes": src["size_bytes"]})
    run_jobs(db, (nb2, ["learner", "summary"]))
    job = db.tables["jobs"][-1]
    assert job["status"] == "done", job.get("error")
    assert not spend                                   # nothing was converted
    assert db.tables["chapter_docs"] == docs           # nothing re-published
    assert len(db.tables["chapters"]) == 2             # linked to the same two chapters
    assert {s["chapter_id"] for s in db.tables["sources"] if s["notebook_id"] == nb2} == \
           {c["id"] for c in db.tables["chapters"]}


def test_a_new_level_only_generates_what_is_missing(web, monkeypatch):
    db, nb = web
    run_jobs(db, (nb, ["learner"]))
    import backend.pipeline.run as run
    seen = []
    real = run.convert_chapter
    monkeypatch.setattr(run, "convert_chapter", lambda ch, levels, *a, **k: (seen.append(levels), real(ch, levels, *a, **k))[1])
    run_jobs(db, (nb, ["summary"]))
    assert seen == [["summary"], ["summary"]]          # learner is already there, so it is not re-uploaded
    assert {d["level"] for d in db.tables["chapter_docs"]} == {"learner", "quiz", "summary"}


def test_one_failed_chapter_does_not_block_the_next(web, monkeypatch):
    db, nb = web
    import backend.pipeline.run as run
    real = run.convert_chapter
    calls = []

    def flaky(ch, *a, **k):
        calls.append(ch.key)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return real(ch, *a, **k)
    monkeypatch.setattr(run, "convert_chapter", flaky)
    run_jobs(db, (nb, ["learner"]))
    job = db.tables["jobs"][0]
    first, second = db.tables["chapters"]
    assert first["status"] == "failed" and "boom" in first["error"]
    assert second["status"] == "ready"
    assert job["status"] == "failed" and "1 of 2 chapters failed" in job["error"]


def test_sha_is_computed_for_sources_without_one(web):
    db, nb = web
    w = run_jobs(db)       # no jobs; just exercise ensure_chapter directly
    rows = w.fetch_sources(nb)
    chapter = w.ensure_chapter(*rows[0])
    again = w.ensure_chapter(*rows[0])
    assert chapter["id"] == again["id"] and len(db.tables["chapters"]) == 1
    assert db.tables["sources"][0]["sha"] == chapter["sha"]


def test_job_failure_is_reported(web, monkeypatch):
    db, nb = web
    db.tables["sources"].clear()
    db.tables["jobs"].append({"id": "job-2", "notebook_id": nb, "levels": ["learner"], "status": "queued"})
    w = worker_mod.Worker(db, worker_id="test")
    w.once = True
    w.job_loop()
    job = db.tables["jobs"][0]
    assert job["status"] == "failed" and "no PDFs" in job["error"]
    assert db.tables["notebooks"][0]["status"] == "failed"


class StreamLLM:
    backend = "ollama_local"
    cfg = {"kind": "ollama"}

    def __init__(self):
        self.messages = None

    def stream(self, messages, **kw):
        self.messages = messages
        yield "A page table "
        yield "maps pages to frames."


def test_explain_request_streams_answer(web, monkeypatch):
    db, nb = web
    db.tables["jobs"].append({"id": "job-3", "notebook_id": nb, "levels": ["learner"], "status": "queued"})
    w = worker_mod.Worker(db, worker_id="test")
    w.once = True
    w.job_loop()
    llm = StreamLLM()
    monkeypatch.setattr(w, "explain_llm", lambda: llm)
    db.tables["explain_requests"].append({"id": "ex-1", "notebook_id": nb, "level": "learner", "section": 0,
                                          "chapter_id": db.tables["chapters"][0]["id"],
                                          "selection": "Inline math", "status": "queued", "cache_key": "k1"})
    w.explain_loop()
    row = db.tables["explain_requests"][0]
    assert row["status"] == "done" and row["answer"] == "A page table maps pages to frames."
    assert row["source"] == "local"
    assert db.tables["explain_cache"][0]["key"] == "k1"
    user = llm.messages[1]["content"]
    assert "SLIDES:" in user and "Inline math" in user and "A·" in user


def test_replace_section_keeps_anchors():
    out = replace_section(DOC, 1, "## TLB\n\nRewritten.\n\n---")
    secs = split_sections(out)
    assert [ids for ids, _ in secs] == [["A·2", "A·3", "A·4", "B·7"], ["B·8"]]
    assert "Rewritten." in secs[1][1] and "A cache of translations" not in out
    first = replace_section(DOC, 0, "## Paging\n\nNew.")
    assert split_sections(first)[0][1].startswith("## Paging\n\nNew.") and "A cache of translations" in first
    with pytest.raises(ExplainError):
        replace_section(DOC, 5, "x")


def test_explain_uses_the_notebooks_section_choice(web, monkeypatch):
    db, nb = web
    run_jobs(db, (nb, ["learner"]))
    chapter = db.tables["chapters"][0]
    db.tables["section_choices"].append({"notebook_id": nb, "chapter_id": chapter["id"], "section": 0,
                                         "level": "proficient"})
    db.tables["section_variants"].append({"chapter_id": chapter["id"], "section": 0, "level": "proficient",
                                          "body": "## Dense\n\nREWRITTEN-SENTINEL text."})
    llm = StreamLLM()
    w = worker_mod.Worker(db, worker_id="test")
    monkeypatch.setattr(w, "explain_llm", lambda: llm)
    db.tables["explain_requests"].append({"id": "ex-3", "notebook_id": nb, "level": "learner", "section": 0,
                                          "chapter_id": chapter["id"], "selection": "x", "status": "queued"})
    w.once = True
    w.explain_loop()
    assert db.tables["explain_requests"][0]["status"] == "done"
    assert "REWRITTEN-SENTINEL" in llm.messages[1]["content"]


def test_explain_bad_request_fails_cleanly(web):
    db, nb = web
    db.tables["jobs"].append({"id": "job-4", "notebook_id": nb, "levels": ["learner"], "status": "queued"})
    w = worker_mod.Worker(db, worker_id="test")
    w.once = True
    w.job_loop()
    db.tables["explain_requests"].append({"id": "ex-2", "notebook_id": nb, "level": "learner", "section": 99,
                                          "chapter_id": db.tables["chapters"][0]["id"],
                                          "selection": "x", "status": "queued"})
    w.explain_loop()
    row = db.tables["explain_requests"][0]
    assert row["status"] == "failed" and "section 99" in row["error"]


# ---------- explain context ----------

DOC = """# T

<!-- slides A·2-4, B·7 -->
## Paging

Pages map to frames.

<!-- slides B·8 -->
## TLB

A cache of translations.
"""

EXTRACTS = {"A·2": {"title": "Paging", "bullets": ["- page = fixed-size block"], "slide_role": "content"},
            "B·8": {"title": "TLB", "bullets": ["- caches PTEs"]}}


def test_expand_and_split():
    assert expand_ids("A·2-4, B·7") == ["A·2", "A·3", "A·4", "B·7"]
    secs = split_sections(DOC)
    assert [ids for ids, _ in secs] == [["A·2", "A·3", "A·4", "B·7"], ["B·8"]]
    assert secs[1][1].startswith("## TLB")


def test_build_messages_grounded_and_validated(tmp_path):
    msgs = build_messages(DOC, EXTRACTS, ExplainRequest(section=1, selection="cache of translations"),
                          lambda ref: None)
    user = msgs[1]["content"]
    assert "caches PTEs" in user and "page = fixed-size" not in user   # only this section's slides
    assert "slide_role" not in user
    img = tmp_path / "f.png"
    img.write_bytes(b"\x89PNG")
    msgs = build_messages(DOC, EXTRACTS, ExplainRequest(section=0, image="assets/A-002-f1.png"), lambda ref: img)
    assert msgs[1]["content"][1]["type"] == "image_url"
    with pytest.raises(ExplainError):
        build_messages(DOC, EXTRACTS, ExplainRequest(section=0, image="../../etc/passwd"), lambda ref: img)
    with pytest.raises(ExplainError):
        build_messages(DOC, EXTRACTS, ExplainRequest(section=0), lambda ref: None)
    follow = ExplainRequest(section=0, selection="x", history=[{"role": "assistant", "content": "A."},
                                                               {"role": "user", "content": "Why?"}])
    assert build_messages(DOC, EXTRACTS, follow, lambda ref: None)[-1]["content"] == "Why?"
    too_many = ExplainRequest(section=0, selection="x", history=[{"role": "user", "content": "q"}] * 4)
    with pytest.raises(ExplainError, match="follow-up"):
        build_messages(DOC, EXTRACTS, too_many, lambda ref: None)


# ---------- quiz ----------

QUIZ_DOC = """## Contents
- x

## Chapter 1 · Processes

text

##### Check your understanding

1.  What does fork return?
2.  Why close fd 1?

<details><summary>Answer</summary>

1.  The child's pid to the parent, 0 to the child.
2.  So open() reuses it.

</details>

---

## Chapter 2 · Sync

#### Check your understanding
1. Why is `counter++` a critical section?
<details><summary>Answer</summary>

It is read-modify-write.

</details>

2. What does down() do at 0?
<details><summary>Answer</summary>

It blocks.

</details>
"""


def test_extract_quiz_both_layouts():
    quiz = extract_quiz(QUIZ_DOC)
    assert [c["chapter"] for c in quiz] == ["Chapter 1 · Processes", "Chapter 2 · Sync"]
    assert quiz[0]["questions"][0] == {"q": "What does fork return?", "a": "The child's pid to the parent, 0 to the child."}
    assert quiz[0]["questions"][1]["a"] == "So open() reuses it."
    assert quiz[1]["questions"] == [{"q": "Why is `counter++` a critical section?", "a": "It is read-modify-write."},
                                    {"q": "What does down() do at 0?", "a": "It blocks."}]


def test_web_explain_prompt_in_sync():
    """web/lib/explain-prompt.generated.ts must match backend/prompts/explain.md (cloud vs local answers)."""
    from backend.paths import PROJECT_ROOT, PROMPTS_DIR
    generated = (PROJECT_ROOT / "web" / "lib" / "explain-prompt.generated.ts").read_text()
    body = generated.split("export const EXPLAIN_PROMPT = ", 1)[1].rstrip().rstrip(";")
    assert json.loads(body) == (PROMPTS_DIR / "explain.md").read_text(), \
        "run `pnpm sync-prompt` in web/ after editing backend/prompts/explain.md"


def test_web_write_prompts_in_sync():
    """web/lib/write-prompts.generated.ts (per-section rewrites) must match the Python writer's prompts."""
    import re
    from backend.paths import PROJECT_ROOT, PROMPTS_DIR
    generated = (PROJECT_ROOT / "web" / "lib" / "write-prompts.generated.ts").read_text()
    for name in ("common", "learner", "proficient", "donkey"):
        m = re.search(rf"^  {name}: (\".*\"),$", generated, re.M)
        assert m and json.loads(m.group(1)) == (PROMPTS_DIR / f"{name}.md").read_text(), \
            f"run `pnpm sync-prompt` in web/ after editing backend/prompts/{name}.md"


def test_interrupted_job_is_requeued(web, monkeypatch):
    db, nb = web
    db.tables["jobs"].append({"id": "job-5", "notebook_id": nb, "levels": ["learner"], "status": "queued"})
    w = worker_mod.Worker(db, worker_id="test")
    w.once = True

    def interrupted(job):
        raise KeyboardInterrupt
    monkeypatch.setattr(w, "run_job", interrupted)
    w.run(once=True)
    job = db.tables["jobs"][0]
    assert job["status"] == "queued" and job["stage"] == "requeued"
