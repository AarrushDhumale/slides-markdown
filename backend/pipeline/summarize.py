"""Quick-revision summary: condense each (fact-checked) Learner section, plus chapter takeaways.

Summarising the Learner text rather than the slides keeps the summary consistent with the notes
the student already has, and inherits their fact-check.
"""

from __future__ import annotations

import hashlib
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from backend.llm.client import LLM
from backend.llm.usage import BudgetExceeded
from backend.paths import PROMPTS_DIR
from backend.pipeline.assemble import degeneration, normalize_section, strip_degenerate
from backend.pipeline.chapter import Chapter, cache_root
from backend.pipeline.outline import MODE_DIRS, Outline

# Two ways to condense a learner section: the cram-sheet summary, and lecture-style notes (a few diagrams kept)
STYLES = {
    "summary": dict(prompt="summary.md", ratio=8, lo=60, hi=250),
    "notes": dict(prompt="notes.md", ratio=4, lo=80, hi=400),
}


def word_limit(learner_md: str, style: str = "summary") -> int:
    st = STYLES[style]
    return max(st["lo"], min(st["hi"], len(learner_md.split()) // st["ratio"]))


def _cached(chapter: Chapter, name: str, prompt: str, user: str, llm: LLM,
            generate: Callable[[], str], subdir: str = "sections", style: str = "summary") -> str:
    key = hashlib.sha256(f"{prompt}\n{user}\n{llm.generation_id}".encode()).hexdigest()[:16]
    path = cache_root(chapter) / subdir / style / f"{name}-{key}.md"
    if path.exists():
        return path.read_text()
    text = generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return text


def keep_heading(summary_md: str, learner_md: str) -> str:
    """A topic has one title at every level: the summary takes the learner section's heading (the
    summarising model likes to shorten it), whether the summary came fresh or from an older cache."""
    m = re.match(r"\s*(#{2,3} .+)", learner_md)
    if not m:
        return summary_md
    body = re.sub(r"\A\s*#{1,6} .*\n*", "", summary_md, count=1)
    return f"{m.group(1).strip()}\n\n{body.lstrip()}"


def summarize_section(learner_md: str, idx: int, llm: LLM, chapter: Chapter, subdir: str = "sections",
                      style: str = "summary") -> str:
    prompt = (PROMPTS_DIR / STYLES[style]["prompt"]).read_text()
    limit = word_limit(learner_md, style)
    user = f"Word limit: {limit}\n\nDetailed notes section:\n\n{learner_md}"

    def generate() -> str:
        msgs = [{"role": "system", "content": prompt}, {"role": "user", "content": user}]
        out = llm.chat(msgs, max_tokens=int(limit * 2.5) + 400, temperature=0.2)
        if degeneration(out) or len(out.strip()) < 40:
            out = llm.chat(msgs, max_tokens=int(limit * 2.5) + 400, temperature=0.0)
        return strip_degenerate(out) if degeneration(out) else out

    return keep_heading(_cached(chapter, f"{idx:02d}", prompt, user, llm, generate, subdir, style), learner_md)


def chapter_takeaways(section_summaries: list[str], llm: LLM, chapter: Chapter, subdir: str = "sections") -> str:
    prompt = (PROMPTS_DIR / "takeaways.md").read_text()
    user = "\n\n".join(section_summaries)

    def generate() -> str:
        out = llm.chat([{"role": "system", "content": prompt}, {"role": "user", "content": user}],
                       max_tokens=900, temperature=0.2)
        bullets = [ln for ln in out.splitlines() if re.match(r"^\s*([-*]|\d+\.)\s", ln)]
        return "\n".join(bullets[:8])

    return _cached(chapter, "takeaways", prompt, user, llm, generate, subdir)


def notes(outline: Outline, learner_sections: list[str], llm: LLM, chapter: Chapter,
          progress: Callable[[int, int], None] | None = None) -> list[str]:
    """Lecture-style notes per outline section, condensed from the learner sections (no takeaways)."""
    return summarize(outline, learner_sections, llm, chapter, progress, style="notes")[0]


def summarize(outline: Outline, learner_sections: list[str], llm: LLM, chapter: Chapter,
              progress: Callable[[int, int], None] | None = None,
              style: str = "summary") -> tuple[list[str], str]:
    """Returns (condensed section markdown per outline section, chapter takeaways bullet list)."""
    n = len(learner_sections)
    results = [""] * n
    subdir = MODE_DIRS[outline.mode]
    done, lock = 0, threading.Lock()

    def run(i: int) -> None:
        nonlocal done
        learner = normalize_section(learner_sections[i], outline.sections[i].title)
        try:
            if outline.sections[i].kind != "content":
                results[i] = learner  # one-line title/repeat notes are already as short as it gets
            else:
                results[i] = summarize_section(learner, i, llm, chapter, subdir, style)
        except BudgetExceeded:  # a spend cap stops the run; never degrade around it
            raise
        except Exception as e:  # fall back to the learner heading + its bold key terms
            print(f"\n  ! summary of section {i + 1} failed ({type(e).__name__}); using a stub")
            heading = re.match(r"## .*", learner).group(0)
            terms = sorted(set(re.findall(r"\*\*([^*]{2,60})\*\*", learner)))[:12]
            results[i] = heading + "\n\n- Key terms: " + ", ".join(terms)
        with lock:
            done += 1
            if progress:
                progress(done, n + 1)

    with ThreadPoolExecutor(max_workers=llm.max_concurrency) as pool:
        list(pool.map(run, range(n)))
    takeaways = ""
    if style == "summary":  # notes carry no chapter takeaways
        try:
            content = [r for r, sec in zip(results, outline.sections) if sec.kind == "content"]
            takeaways = chapter_takeaways(content, llm, chapter, subdir)
        except BudgetExceeded:  # a spend cap stops the run; never degrade around it
            raise
        except Exception as e:
            print(f"\n  ! chapter takeaways failed ({type(e).__name__}); omitting them")
    if progress:
        progress(n + 1, n + 1)
    return results, takeaways
