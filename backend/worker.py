"""Web app worker (`s2m worker`): runs queued conversion jobs and "Explain this" requests from Supabase.

It only makes outbound HTTPS calls (Supabase REST + Storage, model APIs), so it can run anywhere:
the laptop now, a Cloud Run Job later. Two loops share one process:
  * jobs:    claim the oldest queued job -> download its PDFs -> convert_chapter -> upload outputs
  * explain: claim queued explain requests -> stream a short answer into the row (Realtime pushes it)
plus a heartbeat so the web app can show "generator offline" and route explanations to the cloud.

A notebook is a list of chapters, one per uploaded PDF. A chapter is identified by the PDF's content hash and
lives in a shared library, so a PDF someone already converted is never converted (or paid for) again, and each
chapter is published as soon as it is written.

Local layout (never data/chapters/): data/web/notebooks/<notebook id>/pdfs/ (downloads) and
data/web/library/<chapter slug>/ (the pipeline's working dir for that chapter).
Storage layout: pdfs/<notebook>/<source>.pdf (uploaded by the browser) and
library/<chapter slug>/{<level>.md, assets/*, slides/<A-004>.webp, slides.json, extracts.json, quiz.json}.
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import httpx
import pymupdf as fitz
from PIL import Image

from backend.llm import usage
from backend.llm.client import make_llm, stage_llm
from backend.llm.usage import BudgetExceeded
from backend.paths import DATA_ROOT
from backend.pipeline.chapter import Chapter, slide_file_id
from backend.pipeline.explain import (ExplainError, ExplainRequest, build_messages, explain, replace_section,
                                       split_sections)
from backend.pipeline.ingest import file_sha
from backend.pipeline.quiz import extract_quiz

WEB_DIR = DATA_ROOT / "web" / "notebooks"
LIBRARY_DIR = DATA_ROOT / "web" / "library"
CHAPTER_WAIT = 20 * 60             # seconds to wait for another job generating the same chapter
JOB_POLL = 5.0
EXPLAIN_POLL = (1.0, 3.0)          # busy / idle poll interval, seconds
HEARTBEAT_EVERY = 20.0
PROGRESS_EVERY = 1.0               # throttle job progress writes
PARTIAL_EVERY = 0.3                # throttle streamed explanation writes
SLIDE_IMAGE_WIDTH = 1200
LAYOUTS = ("topics", "chapters")
MAX_PAGES = 300                    # per notebook: bounds the cost of one job


class SupabaseError(RuntimeError):
    pass


class Supabase:
    """The few PostgREST + Storage calls the worker needs, with the service-role key (bypasses RLS)."""

    def __init__(self, url: str, key: str):
        self.url = url.rstrip("/")
        self.http = httpx.Client(timeout=60, headers={"apikey": key, "Authorization": f"Bearer {key}"})

    def _check(self, r: httpx.Response) -> httpx.Response:
        if r.status_code >= 400:
            raise SupabaseError(f"{r.request.method} {r.request.url.path}: {r.status_code} {r.text[:300]}")
        return r

    def rpc(self, fn: str, params: dict | None = None):
        return self._check(self.http.post(f"{self.url}/rest/v1/rpc/{fn}", json=params or {})).json()

    def select(self, table: str, query: str) -> list[dict]:
        return self._check(self.http.get(f"{self.url}/rest/v1/{table}?{query}")).json()

    def insert(self, table: str, row: dict) -> None:
        self._check(self.http.post(f"{self.url}/rest/v1/{table}", json=row, headers={"Prefer": "return=minimal"}))

    def update(self, table: str, match: dict, data: dict) -> None:
        q = "&".join(f"{k}=eq.{quote(str(v))}" for k, v in match.items())
        self._check(self.http.patch(f"{self.url}/rest/v1/{table}?{q}", json=data,
                                    headers={"Prefer": "return=minimal"}))

    def upsert(self, table: str, rows: dict | list[dict], on_conflict: str) -> None:
        self._check(self.http.post(f"{self.url}/rest/v1/{table}?on_conflict={on_conflict}", json=rows,
                                   headers={"Prefer": "resolution=merge-duplicates,return=minimal"}))

    def download(self, bucket: str, path: str) -> bytes:
        return self._check(self.http.get(f"{self.url}/storage/v1/object/{bucket}/{quote(path)}")).content

    def upload(self, bucket: str, path: str, data: bytes, content_type: str) -> None:
        self._check(self.http.post(f"{self.url}/storage/v1/object/{bucket}/{quote(path)}", content=data,
                                   headers={"Content-Type": content_type, "x-upsert": "true",
                                            "Cache-Control": "max-age=3600"}))


@dataclass
class LibraryChapter(Chapter):
    """One PDF of the shared library, kept under data/web/library/<key> instead of data/chapters/.
    Its caches live in that folder, so every notebook containing the PDF reuses them."""
    key: str = ""
    root: Path = LIBRARY_DIR

    @property
    def dir(self) -> Path:
        return self.root / self.key


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_name(name: str) -> str:
    stem = re.sub(r"[^\w .()-]+", "_", Path(name).stem).strip() or "slides"
    return stem[:80] + ".pdf"


def _content_type(path: Path) -> str:
    return {".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg", ".md": "text/markdown",
            ".json": "application/json"}.get(path.suffix, "application/octet-stream")


class Worker:
    def __init__(self, db: Supabase, backend: str | None = None, worker_id: str | None = None,
                 layout: str = "topics"):
        if layout not in LAYOUTS:
            raise ValueError(f"layout must be one of {LAYOUTS}")
        self.db = db
        self.backend = backend
        self.layout = layout  # "topics": every PDF merged into one flat book of topics; "chapters": legacy
        self.worker_id = worker_id or socket.gethostname()
        self.running_job: dict | None = None
        self.once = False
        self.stop = threading.Event()

    # ---------- conversion jobs ----------

    def notebook_dir(self, notebook_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f-]{36}", notebook_id):
            raise ValueError(f"bad notebook id {notebook_id!r}")
        return WEB_DIR / notebook_id

    def fetch_sources(self, notebook_id: str) -> list[tuple[dict, Path]]:
        rows = self.db.select("sources", f"notebook_id=eq.{notebook_id}&order=position.asc,created_at.asc")
        if not rows:
            raise ValueError("this notebook has no PDFs")
        pdf_dir = self.notebook_dir(notebook_id) / "pdfs"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        found, used, total = [], set(), 0
        for row in rows:
            name = _safe_name(row["filename"])
            while name in used:  # two uploads with the same filename stay separate decks
                name = f"{Path(name).stem}-{len(used)}.pdf"
            used.add(name)
            path = pdf_dir / name
            if not path.exists() or path.stat().st_size != row.get("size_bytes", -1):
                path.write_bytes(self.db.download("pdfs", row["storage_path"]))
            try:
                with fitz.open(path) as doc:
                    pages = doc.page_count
            except Exception as e:
                raise ValueError(f"{row['filename']} is not a readable PDF ({e})") from e
            if row.get("pages") != pages:
                self.db.update("sources", {"id": row["id"]}, {"pages": pages})
                row["pages"] = pages
            found.append((row, path))
            total += pages
        if total > MAX_PAGES:
            raise ValueError(f"this notebook has {total} slides; the limit is {MAX_PAGES} per notebook")
        self.db.update("notebooks", {"id": notebook_id}, {"slide_count": total})
        return found

    def ensure_chapter(self, row: dict, path: Path) -> dict:
        """The library row for a source's PDF (created here if the web app has not registered it yet)."""
        if row.get("chapter_id"):
            found = self.db.select("chapters", f"id=eq.{row['chapter_id']}")
            if found:
                return found[0]
        sha = file_sha(path)
        found = self.db.select("chapters", f"sha=eq.{sha}")
        if not found:
            stem = Path(row["filename"]).stem
            base = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:60].strip("-")
            try:
                self.db.insert("chapters", {"sha": sha, "slug": f"{base}-{sha[:8]}" if base else sha[:8],
                                            "title": stem[:200] or "Slides", "filename": row["filename"],
                                            "pages": row.get("pages")})
            except SupabaseError:  # the web app registered it a moment ago
                pass
            found = self.db.select("chapters", f"sha=eq.{sha}")
        self.db.update("sources", {"id": row["id"]}, {"sha": sha, "chapter_id": found[0]["id"]})
        if not found[0].get("pages") and row.get("pages"):  # the web app registered it before the page count was known
            self.db.update("chapters", {"id": found[0]["id"]}, {"pages": row["pages"]})
            found[0]["pages"] = row["pages"]
        return found[0]

    def missing_levels(self, chapter: dict, levels: list[str]) -> list[str]:
        """The levels still to generate for this chapter. The summary and notes are condensed from the
        learner doc (and the quiz comes from it), so a missing learner doc is made along with them."""
        have = {d["level"] for d in self.db.select("chapter_docs", f"chapter_id=eq.{chapter['id']}")}
        need = [lv for lv in levels if lv not in have and lv != "quiz"]
        if {"summary", "notes"} & set(need) and "learner" not in have and "learner" not in need:
            need.append("learner")
        return need

    def run_job(self, job: dict) -> None:
        nb = job["notebook_id"]
        chapters, seen = [], set()
        for row, path in self.fetch_sources(nb):
            chapter = self.ensure_chapter(row, path)
            if chapter["id"] not in seen:  # the same PDF twice in a notebook is one chapter
                seen.add(chapter["id"])
                chapters.append((chapter, path))
        current: list[dict] = [{}]
        last = [0.0]

        def progress(stage: str, done: int, total: int) -> None:
            if stage == "done":  # one chapter finished; the job finishes after the last one
                return
            now = time.monotonic()
            if now - last[0] < PROGRESS_EVERY and done != total:
                return
            last[0] = now
            try:
                self.db.update("jobs", {"id": job["id"]}, {
                    "stage": stage, "done": done, "total": total, "heartbeat_at": _now(),
                    "cost_usd": round(usage.job_spend(job["id"]), 6)})
                if current[0]:
                    self.db.update("chapters", {"id": current[0]["id"]}, {"heartbeat_at": _now()})
            except (SupabaseError, httpx.HTTPError) as e:  # progress is cosmetic; never fail the job on it
                print(f"  ! progress update failed: {e}")

        usage.set_job(job["id"])
        failures = []
        try:
            for i, (chapter, path) in enumerate(chapters):
                current[0] = chapter
                self.db.update("jobs", {"id": job["id"]}, {
                    "chapter_id": chapter["id"], "chapters_done": i, "chapters_total": len(chapters),
                    "heartbeat_at": _now()})
                try:
                    self.build_chapter(job, chapter, path, progress)
                except (BudgetExceeded, KeyboardInterrupt):
                    self._release_chapter(chapter)
                    raise
                except Exception as e:  # one bad chapter must not stop the others
                    traceback.print_exc()
                    message = f"{type(e).__name__}: {e}"
                    self.db.update("chapters", {"id": chapter["id"]}, {
                        "status": "failed", "error": message[:500], "updated_at": _now()})
                    failures.append(f"{chapter['title']}: {message}")
            if failures:
                raise RuntimeError(f"{len(failures)} of {len(chapters)} chapters failed. " + "; ".join(failures))
            self.db.update("jobs", {"id": job["id"]}, {
                "status": "done", "stage": "done", "finished_at": _now(), "error": None,
                "chapters_done": len(chapters), "chapters_total": len(chapters),
                "cost_usd": round(usage.job_spend(job["id"]), 6)})
            self.db.update("notebooks", {"id": nb}, {"status": "ready", "updated_at": _now()})
        finally:
            usage.set_job(None)

    def build_chapter(self, job: dict, chapter: dict, path: Path, progress) -> None:
        """Generate what this chapter is missing and publish it; a chapter that already has every
        requested level (made for any notebook) costs nothing."""
        from backend.pipeline.run import convert_chapter

        deadline = time.monotonic() + CHAPTER_WAIT
        while True:
            need = self.missing_levels(chapter, list(job["levels"]))
            if not need:
                print(f"   chapter {chapter['slug']}: already generated")
                return
            if self.db.rpc("claim_chapter", {"p_chapter": chapter["id"], "p_job": job["id"]}):
                break
            if time.monotonic() > deadline:  # another job is generating it; its result is used once it lands
                raise RuntimeError("another job has been generating this chapter for too long")
            self.stop.wait(JOB_POLL)
        print(f"   chapter {chapter['slug']}: generating {need}")
        library = LibraryChapter(chapter["title"], [path], key=chapter["slug"], root=LIBRARY_DIR)
        outputs = convert_chapter(library, need, self.backend, progress, by_topic=self.layout == "topics")
        self.upload_chapter(chapter, library, outputs)

    def _release_chapter(self, chapter: dict) -> None:
        """Hand a chapter back after an interrupt or spend cap, so the next job can generate it."""
        try:
            self.db.update("chapters", {"id": chapter["id"]}, {"status": "new", "generating_job": None})
        except (SupabaseError, httpx.HTTPError):
            pass

    def _upload_once(self, root: Path, bucket: str, key: str, path: Path, data: bytes | None = None,
                     content_type: str | None = None) -> None:
        """Upload unless this exact file (size + mtime) was already uploaded from this machine."""
        index_path = root / "uploaded.json"
        index = json.loads(index_path.read_text()) if index_path.exists() else {}
        stamp = f"{path.stat().st_size}:{path.stat().st_mtime_ns}"
        if index.get(key) == stamp:
            return
        self.db.upload(bucket, key, data if data is not None else path.read_bytes(),
                       content_type or _content_type(path))
        index[key] = stamp
        index_path.write_text(json.dumps(index))

    def upload_chapter(self, chapter: dict, library: LibraryChapter, outputs: dict[str, Path]) -> None:
        """Publish a chapter's files to the library bucket, then its chapter_docs rows (which is what
        the web app listens to), so it shows up in every notebook containing it right away."""
        prefix, root = chapter["slug"], library.dir
        assets = library.dir / "assets"
        for f in sorted(assets.glob("*")) if assets.exists() else []:
            self._upload_once(root, "library", f"{prefix}/assets/{f.name}", f)
        slides = json.loads((library.dir / "slides.json").read_text())
        extracts = json.loads((library.dir / "extracts.json").read_text())
        index = {}
        for sid, info in slides.items():
            src = Path(info["image"])
            key = f"{prefix}/slides/{slide_file_id(sid)}.webp"
            if src.exists() and not info.get("duplicate_of"):
                img = Image.open(src).convert("RGB")
                if img.width > SLIDE_IMAGE_WIDTH:
                    img = img.resize((SLIDE_IMAGE_WIDTH, round(img.height * SLIDE_IMAGE_WIDTH / img.width)))
                buf = BytesIO()
                img.save(buf, "WEBP", quality=80)
                self._upload_once(root, "library", key, src, buf.getvalue(), "image/webp")
            target = info.get("duplicate_of") or sid
            index[sid] = {"image": f"slides/{slide_file_id(target)}.webp",
                          "title": (extracts.get(sid) or extracts.get(target) or {}).get("title", "")}
        self.db.upload("library", f"{prefix}/slides.json", json.dumps(index, ensure_ascii=False).encode(),
                       "application/json")
        self.db.upload("library", f"{prefix}/extracts.json", json.dumps(extracts, ensure_ascii=False).encode(),
                       "application/json")
        docs, title = [], None
        for level, path in outputs.items():
            if level == "papers":
                continue
            md = path.read_text()
            title = title or next((ln[2:].strip() for ln in md.splitlines() if ln.startswith("# ")), None)
            self.db.upload("library", f"{prefix}/{level}.md", md.encode(), "text/markdown; charset=utf-8")
            docs.append({"chapter_id": chapter["id"], "level": level, "storage_path": f"{prefix}/{level}.md",
                         "word_count": len(md.split()), "sections": md.count("<!-- slides "),
                         "updated_at": _now()})
            if level == "learner":
                quiz = extract_quiz(md)
                self.db.upload("library", f"{prefix}/quiz.json", json.dumps(quiz, ensure_ascii=False).encode(),
                               "application/json")
                docs.append({"chapter_id": chapter["id"], "level": "quiz", "storage_path": f"{prefix}/quiz.json",
                             "word_count": sum(len(c["questions"]) for c in quiz), "sections": len(quiz),
                             "updated_at": _now()})
        if docs:
            self.db.upsert("chapter_docs", docs, "chapter_id,level")
        update = {"status": "ready", "generating_job": None, "error": None, "updated_at": _now()}
        if title:
            update["title"] = title[:200]
        self.db.update("chapters", {"id": chapter["id"]}, update)

    def job_loop(self) -> None:
        while not self.stop.is_set():
            try:
                rows = self.db.rpc("claim_job", {"p_worker": self.worker_id})
            except (SupabaseError, httpx.HTTPError) as e:
                print(f"  ! could not poll jobs: {e}")
                self.stop.wait(JOB_POLL * 3)
                continue
            job = rows[0] if isinstance(rows, list) and rows else None
            if not job:
                if self.once:
                    return
                self.stop.wait(JOB_POLL)
                continue
            self.running_job = job
            print(f"== job {job['id']} notebook {job['notebook_id']} levels {job['levels']}")
            try:
                self.run_job(job)
                print(f"   done (${usage.job_spend(job['id']):.4f})")
            except KeyboardInterrupt:
                self._requeue(job)
                raise
            except BudgetExceeded as e:
                self._fail(job, f"Spend cap reached: {e}")
            except Exception as e:  # any failure is reported on the job; the worker keeps going
                traceback.print_exc()
                self._fail(job, f"{type(e).__name__}: {e}")
            finally:
                self.running_job = None

    def _fail(self, job: dict, message: str) -> None:
        print(f"   failed: {message}")
        try:
            self.db.update("jobs", {"id": job["id"]}, {
                "status": "failed", "error": message[:1000], "finished_at": _now(),
                "cost_usd": round(usage.job_spend(job["id"]), 6)})
            self.db.update("notebooks", {"id": job["notebook_id"]}, {"status": "failed"})
        except (SupabaseError, httpx.HTTPError) as e:
            print(f"  ! could not mark job failed: {e}")

    # ---------- explain requests ----------

    def _local_busy(self) -> bool:
        """A conversion is running on the local model (only one model fits the laptop GPU)."""
        if not self.running_job:
            return False
        try:
            return any(m.backend == "ollama_local" for m in self._stage_members("write"))
        except Exception:
            return False

    def _stage_members(self, stage: str):
        llm = stage_llm(stage, self.backend)
        return getattr(llm, "members", [llm])

    def explain_llm(self):
        """explain_chain (local first), or straight to its cloud member while the GPU is busy."""
        llm = stage_llm("explain")
        if self._local_busy():
            members = [m for m in getattr(llm, "members", [llm]) if m.cfg["kind"] != "ollama"]
            if members:
                return members[0]
        return llm

    def _chapter_file(self, slug: str, rel: str) -> Path:
        """A generated file of a library chapter: the local working copy if present, else fetched from Storage."""
        local = LIBRARY_DIR / slug / rel
        if not local.exists():
            cached = LIBRARY_DIR / slug / "fetched" / rel
            if not cached.exists():
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_bytes(self.db.download("library", f"{slug}/{rel}"))
            return cached
        return local

    def answer_explain(self, row: dict) -> None:
        nb, chapter_id = row["notebook_id"], row.get("chapter_id")
        found = self.db.select("chapters", f"id=eq.{chapter_id}") if chapter_id else []
        if not found:
            raise ExplainError("chapter not found")
        slug = found[0]["slug"]
        req = ExplainRequest(section=row["section"], selection=row.get("selection"), image=row.get("image"),
                             history=row.get("history") or [], simpler=bool(row.get("simpler")))
        doc = self._chapter_file(slug, f"{row['level']}.md").read_text()
        extracts = json.loads(self._chapter_file(slug, "extracts.json").read_text())
        # the reader shows this notebook's per-section level choice; explain what the student is looking at
        choice = self.db.select("section_choices",
                                f"notebook_id=eq.{nb}&chapter_id=eq.{chapter_id}&section=eq.{req.section}")
        if choice:
            variant = self.db.select("section_variants", f"chapter_id=eq.{chapter_id}&section=eq.{req.section}"
                                                         f"&level=eq.{choice[0]['level']}")
            if variant and 0 <= req.section < len(split_sections(doc)):
                doc = replace_section(doc, req.section, variant[0]["body"])

        def resolve_image(ref: str) -> Path | None:
            if ref.startswith("slide:"):
                sid = ref[6:]
                slides_file = LIBRARY_DIR / slug / "slides.json"
                if slides_file.exists():
                    info = json.loads(slides_file.read_text()).get(sid)
                    if info and Path(info["image"]).exists():
                        return Path(info["image"])
                return self._chapter_file(slug, f"slides/{slide_file_id(sid)}.webp")
            return self._chapter_file(slug, ref)

        messages = build_messages(doc, extracts, req, resolve_image)
        llm = self.explain_llm()
        text, last = "", 0.0
        for chunk in explain(llm, messages):
            text += chunk
            if time.monotonic() - last >= PARTIAL_EVERY:
                last = time.monotonic()
                self.db.update("explain_requests", {"id": row["id"]}, {"partial_text": text, "status": "streaming"})
        model = getattr(getattr(llm, "active", llm), "backend", "?")
        where = "local" if getattr(getattr(llm, "active", llm), "cfg", {}).get("kind") == "ollama" else "cloud"
        self.db.update("explain_requests", {"id": row["id"]}, {
            "partial_text": text, "answer": text, "status": "done", "model": model, "source": where,
            "updated_at": _now()})
        if row.get("cache_key") and text.strip():
            self.db.upsert("explain_cache", {"key": row["cache_key"], "answer": text, "model": model,
                                             "source": where}, "key")

    def explain_loop(self) -> None:
        idle = 0
        while not self.stop.is_set():
            try:
                rows = self.db.rpc("claim_explain", {"p_worker": self.worker_id})
            except (SupabaseError, httpx.HTTPError) as e:
                print(f"  ! could not poll explain requests: {e}")
                self.stop.wait(10)
                continue
            row = rows[0] if isinstance(rows, list) and rows else None
            if not row:
                idle += 1
                if self.once:
                    return
                self.stop.wait(EXPLAIN_POLL[0] if idle < 30 else EXPLAIN_POLL[1])
                continue
            idle = 0
            try:
                self.answer_explain(row)
            except BudgetExceeded as e:
                self._explain_failed(row, f"Spend cap reached: {e}")
            except ExplainError as e:
                self._explain_failed(row, str(e))
            except Exception as e:
                traceback.print_exc()
                self._explain_failed(row, f"{type(e).__name__}: {e}")

    def _explain_failed(self, row: dict, message: str) -> None:
        try:
            self.db.update("explain_requests", {"id": row["id"]},
                           {"status": "failed", "error": message[:500], "updated_at": _now()})
        except (SupabaseError, httpx.HTTPError):
            pass

    # ---------- heartbeat ----------

    def local_model_ready(self) -> bool:
        try:
            llm = make_llm("ollama_local")
            r = httpx.get(f"{llm._ollama_url()}/api/tags", timeout=3)
            return r.status_code == 200 and any(m["name"] == llm.cfg["model"] for m in r.json().get("models", []))
        except Exception:
            return False

    def heartbeat(self) -> None:
        try:
            self.db.upsert("worker_status", {
                "id": self.worker_id, "last_seen": _now(), "busy": self.running_job is not None,
                "current_job": self.running_job["id"] if self.running_job else None,
                "local_model": self.local_model_ready() and not self._local_busy(),
                "month_usd": round(usage.month_spend(), 4)}, "id")
        except (SupabaseError, httpx.HTTPError) as e:
            print(f"  ! heartbeat failed: {e}")

    def heartbeat_loop(self) -> None:
        while not self.stop.is_set():
            self.heartbeat()
            self.stop.wait(HEARTBEAT_EVERY)

    def run(self, once: bool = False) -> None:
        self.once = once
        self.heartbeat()
        if not once:
            for target, name in ((self.explain_loop, "explain"), (self.heartbeat_loop, "heartbeat")):
                threading.Thread(target=target, name=name, daemon=True).start()
        try:
            self.job_loop()
            if once:
                self.explain_loop()
        except KeyboardInterrupt:
            print("\nstopping…")
        finally:
            self.stop.set()

    def _requeue(self, job: dict) -> None:
        """Hand an interrupted job back to the queue; its finished slides/sections are cached."""
        print(f"   interrupted; requeueing job {job['id']}")
        try:
            self.db.update("jobs", {"id": job["id"]}, {"status": "queued", "stage": "requeued", "worker": None})
            self.db.update("notebooks", {"id": job["notebook_id"]}, {"status": "queued"})
        except (SupabaseError, httpx.HTTPError):
            pass


def run_worker(backend: str | None = None, once: bool = False, layout: str = "topics") -> None:
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise SystemExit("set SUPABASE_URL and SUPABASE_SERVICE_KEY (e.g. in .env) to run the worker")
    worker = Worker(Supabase(url, key), backend, layout=layout)
    print(f"s2m worker '{worker.worker_id}' polling {url} (Ctrl-C to stop)")
    worker.run(once=once)
