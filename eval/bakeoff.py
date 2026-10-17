"""Compare models on your own slides: readers (extraction) or writers (notes).

Readers: the same pages through several backends, side by side (JSON + timing + tokens + $)

  python eval/bakeoff.py "data/inbox/11 - Paging.pdf" --pages 3,8,30 \
      --backends gemini_25_flash,openrouter_qwen35,dashbw_qwen35

Writers: one reader's extracts and one outline, then each writer writes the same sections
(draft + fact-check, like the real pipeline), with checks for the failures that matter here:
runaway length, missing slides, degenerate text, broken Markdown/LaTeX

  python eval/bakeoff.py "data/inbox/11 - Paging.pdf" --stage write --reader openrouter_qwen35 \
      --writers deepseek_v32,openrouter_qwen35,gemini_flash_lite --sections 3

Results go to eval/results/<deck>/ (gitignored). Paid backends are billed and counted against
S2M_MAX_USD_PER_JOB; `s2m usage` lists the spend afterwards. Keys come from .env.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.llm import usage  # noqa: E402
from backend.llm.client import make_llm  # noqa: E402
from backend.paths import PROJECT_ROOT, PROMPTS_DIR  # noqa: E402
from backend.pipeline.assemble import check_markdown, degeneration, missing_slides  # noqa: E402
from backend.pipeline.chapter import Chapter, build_slides  # noqa: E402
from backend.pipeline.extract import extract_all, extract_slide  # noqa: E402
from backend.pipeline.outline import build_outline  # noqa: E402
from backend.pipeline.run import TOPIC_LAYOUT  # noqa: E402
from backend.pipeline.write import review_section, section_budget, write_section  # noqa: E402

RESULTS = Path(__file__).parent / "results"


@dataclass
class ScratchChapter(Chapter):
    """A chapter kept under `root` (never data/chapters/), so a run leaves the user's docs alone."""
    root: Path = Path(".")

    @property
    def dir(self) -> Path:
        return self.root


def ledger_totals(job: str) -> dict:
    """Tokens and dollars the usage ledger recorded for one bake-off job."""
    t = {"calls": 0, "in": 0, "out": 0, "usd": 0.0}
    if usage.LEDGER.exists():
        for line in usage.LEDGER.read_text().splitlines():
            row = json.loads(line)
            if row.get("job") == job:
                t["calls"] += 1
                t["in"] += row.get("in", 0)
                t["out"] += row.get("out", 0)
                t["usd"] += row.get("usd", 0.0)
    return t


def section_metrics(md: str, budget: int, slide_ids: list[str], extracts: dict) -> dict:
    words = len(md.split())
    return {
        "words": words,
        "budget": budget,
        "of_budget": round(words / budget, 2) if budget else None,
        "degenerate": degeneration(md) or "",
        "missing_slides": missing_slides(md, slide_ids, extracts),
        "render_problems": check_markdown(md),
        "figures": len(re.findall(r"!\[", md)),
        "beyond_the_slides": md.count("Beyond the slides"),
    }


def issues(m: dict) -> list[str]:
    out = []
    if m["degenerate"]:
        out.append(f"degenerate ({m['degenerate']})")
    if m["missing_slides"]:
        out.append(f"skipped {', '.join(m['missing_slides'])}")
    if m["render_problems"]:
        out.append("; ".join(m["render_problems"]))
    if m["of_budget"] and m["of_budget"] > 1.35:
        out.append(f"{m['of_budget']}x over budget")
    return out


def extract_stage(args) -> dict[str, dict]:
    pdf = Path(args.pdf)
    out_dir = args.out / pdf.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    slides = {s.page: s for s in build_slides(ScratchChapter(pdf.stem, [pdf], root=out_dir / "_ingest").ingest())}
    pages = [int(p) for p in args.pages.split(",")]
    prompt = (PROMPTS_DIR / "extract.md").read_text()
    run = uuid.uuid4().hex[:8]  # job ids must be unique per invocation, or totals include earlier runs

    timings: dict[str, list[float]] = {}
    totals: dict[str, dict] = {}
    for name in args.backends.split(","):
        llm = make_llm(name, "extract")
        llm.connect()
        job = f"bakeoff:{run}:extract:{name}"
        usage.set_job(job)
        for page in pages:
            t0 = time.time()
            try:
                result = extract_slide(slides[page], llm, prompt).model_dump()
            except Exception as e:
                result = {"error": f"{type(e).__name__}: {e}"}
            dt = time.time() - t0
            timings.setdefault(name, []).append(dt)
            (out_dir / f"p{page:03d}.{name}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
            print(f"{name:22} p{page:<3} {dt:6.1f}s  figures={len(result.get('figures', []))}"
                  + (f"  ERROR {result['error'][:80]}" if "error" in result else ""))
        usage.set_job(None)
        totals[name] = ledger_totals(job)

    for page in pages:
        shutil.copyfile(slides[page].image_path, out_dir / f"p{page:03d}.png")
        md = [f"# Page {page}", f"![slide](p{page:03d}.png)"]
        for name in timings:
            md.append(f"## {name}\n```json\n{(out_dir / f'p{page:03d}.{name}.json').read_text()}\n```")
        (out_dir / f"p{page:03d}.md").write_text("\n\n".join(md))
    print("\nper slide (first call includes model load; $ is 0 for local/free backends):")
    print(f"  {'backend':22} {'sec':>6} {'tokens in/out':>16} {'$/slide':>9} {'~$/100 slides':>14}")
    for name, ts in timings.items():
        t = totals[name]
        n = len(ts)
        print(f"  {name:22} {sum(ts) / n:6.1f} {t['in'] // n:>8}/{t['out'] // n:<7} {t['usd'] / n:9.5f} {100 * t['usd'] / n:14.3f}")
    print(f"\nopen {out_dir}/pNNN.md to compare the JSON against the slide image")
    return {name: {"slides": len(ts), **totals[name]} for name, ts in timings.items()}


def write_stage(args) -> list[dict]:
    pdf = Path(args.pdf)
    out_dir = args.out / pdf.stem / "write"
    out_dir.mkdir(parents=True, exist_ok=True)
    names = args.writers.split(",")
    run = uuid.uuid4().hex[:8]

    chapter = ScratchChapter(pdf.stem, [pdf], root=out_dir / "_shared")
    slides = build_slides(chapter.ingest())
    reader = make_llm(args.reader, "extract")
    extracts = extract_all(slides, reader)
    outline = build_outline(chapter, slides, extracts, make_llm(names[0], "outline"), reader.model_id, **TOPIC_LAYOUT)
    picks = [i for i, s in enumerate(outline.sections) if s.kind == "content"][: args.sections]
    slides_by_id = {s.id: s for s in slides}
    print(f"{len(outline.sections)} topic sections; writing {len(picks)}: "
          + "; ".join(outline.sections[i].title for i in picks))

    rows = []
    for name in names:
        llm = make_llm(name, "write")
        writer_chapter = ScratchChapter(pdf.stem, [pdf], root=out_dir / name)
        job = f"bakeoff:{run}:write:{name}"
        usage.set_job(job)
        t0 = time.time()
        parts, section_rows = [], []
        for i in picks:
            sec = outline.sections[i]
            try:
                md = write_section(outline, i, args.level, extracts, slides_by_id, llm, writer_chapter)
                if not args.no_review:
                    md = review_section(md, outline, i, args.level, extracts, llm, writer_chapter)
                m = section_metrics(md, section_budget(outline, i, args.level, extracts), sec.slides, extracts)
            except Exception as e:
                md, m = f"_FAILED: {type(e).__name__}: {e}_", None
            section_rows.append(m)
            parts.append(f"<!-- section {i + 1}: {sec.title} -->\n{md}\n\n<!-- metrics: {json.dumps(m)} -->")
        usage.set_job(None)
        dt = time.time() - t0
        t = ledger_totals(job)
        (out_dir / f"{name}.md").write_text("\n\n---\n\n".join(parts))
        ok = [m for m in section_rows if m]
        rows.append({
            "writer": name, "sec": dt, "tokens_in": t["in"], "tokens_out": t["out"], "usd": t["usd"],
            "words": sum(m["words"] for m in ok), "budget": sum(m["budget"] for m in ok),
            "issues": [f"s{i + 1}: {x}" for i, m in zip(picks, section_rows) for x in (issues(m) if m else ["FAILED"])],
        })

    lines = ["| writer | seconds | tokens in / out | $ | words / budget | issues |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['writer']} | {r['sec']:.0f} | {r['tokens_in']:,} / {r['tokens_out']:,} | {r['usd']:.4f} | "
                     f"{r['words']} / {r['budget']} | {'; '.join(r['issues']) or 'none'} |")
    summary = "\n".join(lines)
    (out_dir / "summary.md").write_text(f"# Writer bake-off: {pdf.stem}\n\nReader: {args.reader}. Level: {args.level}. "
                                        f"Sections: {len(picks)} of {len(outline.sections)}.\n\n{summary}\n")
    print("\n" + summary)
    print(f"\nread the notes side by side: {out_dir}/<writer>.md  (check tone, LaTeX, 'Beyond the slides' discipline)")
    return rows


def main(argv=None) -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--stage", choices=["extract", "write"], default="extract")
    ap.add_argument("--pages", help="extract: comma list of 1-based pages")
    ap.add_argument("--backends", help="extract: comma list of backend names")
    ap.add_argument("--reader", help="write: backend whose extracts every writer gets")
    ap.add_argument("--writers", help="write: comma list of backend names")
    ap.add_argument("--sections", type=int, default=2, help="write: how many topic sections to write (default 2)")
    ap.add_argument("--level", default="learner", choices=["proficient", "learner", "dockey"])
    ap.add_argument("--no-review", action="store_true", help="write: skip the fact-check pass")
    ap.add_argument("--out", type=Path, default=RESULTS, help="results folder (default eval/results)")
    args = ap.parse_args(argv)
    if args.stage == "extract":
        if not (args.pages and args.backends):
            ap.error("--stage extract needs --pages and --backends")
        extract_stage(args)
    else:
        if not (args.reader and args.writers):
            ap.error("--stage write needs --reader and --writers")
        write_stage(args)


if __name__ == "__main__":
    main()
