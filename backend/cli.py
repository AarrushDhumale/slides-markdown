"""s2m: convert lecture slide PDFs into level-adapted Markdown study docs.

  s2m convert a.pdf b.pdf --chapter "Ch3 Sorting" --level learner
  s2m inbox --level all                  # every subfolder of data/inbox = one chapter
  s2m usage [--month 2026-10]           # API tokens and $ spent, by backend and job
  s2m worker                             # web app: process queued jobs + "Explain this" requests
  s2m server setup|up|status|down [--backend dashbw]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from backend.paths import OUTPUTS as LEVELS

LEVEL_HELP = {
    "proficient": "high-level overview, assumes familiarity, technical terms",
    "learner": "detailed explanations for a college student new to the topic",
    "donkey": "explain like I'm five: analogies first, formal stuff tucked away",
    "notes": "short lecture notes you could write down, with the key diagrams (from the learner doc)",
    "summary": "concise quick-revision version of the learner doc",
}


def ask_levels() -> list[str]:
    print("Which difficulty level(s)?")
    for i, lvl in enumerate(LEVELS, 1):
        print(f"  {i}. {lvl:<11} {LEVEL_HELP[lvl]}")
    print(f"  {len(LEVELS) + 1}. all")
    while True:
        raw = input("Choose (e.g. 2 or 1,3): ").strip()
        try:
            picks = {int(x) for x in raw.replace(" ", "").split(",") if x}
        except ValueError:
            continue
        if len(LEVELS) + 1 in picks:
            return list(LEVELS)
        if picks and all(1 <= p <= len(LEVELS) for p in picks):
            return [LEVELS[p - 1] for p in sorted(picks)]


def parse_levels(value: str | None) -> list[str]:
    if value is None:
        return ask_levels() if sys.stdin.isatty() else ["learner"]
    if value == "all":
        return list(LEVELS)
    levels = [{"dockey": "donkey"}.get(v.strip(), v.strip()) for v in value.split(",")]  # old name still works
    bad = [v for v in levels if v not in LEVELS]
    if bad:
        raise SystemExit(f"unknown level(s): {bad}; choose from {LEVELS} or 'all'")
    return levels


def print_progress(stage: str, done: int, total: int) -> None:
    if stage == "done":
        print(f"\nfinished in {done}s")
    elif stage == "outline":
        print(f"\noutline: {done} sections")
    else:
        end = "\n" if done == total else ""
        print(f"\r{stage:<18} {done}/{total}", end=end, flush=True)


def run_chapter(chapter, levels, backend, open_result, review=True, per_slide=False, per_deck=False,
                by_topic=False):
    from backend.llm import usage
    from backend.pipeline.run import convert_chapter

    print(f"== {chapter.name}  ({len(chapter.pdf_paths)} PDF(s), levels: {', '.join(levels)})")
    job = f"cli:{chapter.name}:{int(time.time())}"  # S2M_MAX_USD_PER_JOB applies to each chapter
    usage.set_job(job)
    try:
        outputs = convert_chapter(chapter, levels, backend, print_progress, review=review,
                                  per_slide=per_slide, per_deck=per_deck, by_topic=by_topic)
    finally:
        usage.set_job(None)
    for level, path in outputs.items():
        print(f"  {level:<11} -> {path}")
    print(f"  API cost: ${usage.job_spend(job):.4f}  (this month: ${usage.month_spend():.2f})")
    if open_result and outputs:
        subprocess.Popen(["xdg-open", str(next(iter(outputs.values())))],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cmd_convert(args):
    from backend.pipeline.chapter import Chapter

    pdfs = [Path(p) for p in args.pdfs]
    missing = [p for p in pdfs if not p.is_file()]
    if missing:
        raise SystemExit(f"not found: {missing}")
    name = args.chapter or pdfs[0].stem
    papers = [Path(p) for p in args.papers or []]
    missing = [p for p in papers if not p.is_file()]
    if missing:
        raise SystemExit(f"not found: {missing}")
    run_chapter(Chapter(name, pdfs, papers=papers), parse_levels(args.level), args.backend, args.open, not args.no_review,
                args.per_slide, args.per_deck, args.by_topic)


def cmd_inbox(args):
    from backend.pipeline.chapter import discover_inbox

    chapters = discover_inbox()
    if not chapters:
        raise SystemExit("data/inbox is empty: add PDFs (a subfolder per multi-PDF chapter)")
    levels = parse_levels(args.level)
    for ch in chapters:
        run_chapter(ch, levels, args.backend, open_result=False, review=not args.no_review,
                    per_slide=args.per_slide, per_deck=args.per_deck, by_topic=args.by_topic)


def cmd_server(args):
    from backend.llm.client import get_remote

    name = args.backend or "dashbw"
    remote = get_remote(name)
    if args.action == "setup":
        remote.setup()
    elif args.action == "up":
        print(remote.ensure_up())
        print("tunnel stays open while this command runs; Ctrl-C to detach (server keeps running "
              f"until idle for {remote.idle_min} min)")
        try:
            remote._tunnel and remote._tunnel.wait()
        except KeyboardInterrupt:
            pass
    elif args.action == "status":
        print(remote.status())
    elif args.action == "down":
        remote.stop()


def cmd_usage(args):
    from backend.llm import usage

    s = usage.summary(args.month)
    print(f"API usage for {s['month']}: ${s['total']:.4f}")
    for name, b in sorted(s["backends"].items(), key=lambda kv: -kv[1]["usd"]):
        print(f"  {name:<22} {b['calls']:>6} calls  {b['in']:>10,} in  {b['out']:>9,} out  ${b['usd']:.4f}")
    if s["jobs"]:
        print("by job:")
        for job, usd in sorted(s["jobs"].items(), key=lambda kv: -kv[1])[:20]:
            print(f"  {job:<50} ${usd:.4f}")


def cmd_worker(args):
    from backend.worker import run_worker

    run_worker(backend=args.backend, once=args.once, layout=args.layout)


def main(argv=None):
    try:  # API keys and spend caps from .env (see .env.example)
        from dotenv import load_dotenv
        from backend.paths import PROJECT_ROOT
        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass
    p = argparse.ArgumentParser(prog="s2m", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("convert", help="convert one chapter (one or more PDFs, in order)")
    c.add_argument("pdfs", nargs="+")
    c.add_argument("--chapter", help="chapter name (default: first PDF's filename)")
    c.add_argument("--level", help="proficient | learner | donkey | notes | summary | comma list | all (asks if omitted)")
    c.add_argument("--backend", help="backend from backends.yaml for all stages")
    c.add_argument("--papers", nargs="+", metavar="PDF",
                   help="past-year papers (and solution PDFs): adds papers.md and 'Asked in' tags to the docs")
    c.add_argument("--open", action="store_true", help="open the first result when done")
    c.add_argument("--no-review", action="store_true", help="skip the fact-check pass (faster, less faithful)")
    mode = c.add_mutually_exclusive_group()
    mode.add_argument("--per-deck", action="store_true",
                      help="one part per PDF, in the order given, split into topics (writes <level>-by-deck.md)")
    mode.add_argument("--per-slide", action="store_true",
                      help="one section per slide page, in order (writes <level>-by-slide.md)")
    mode.add_argument("--by-topic", action="store_true",
                      help="many small topic sections across all PDFs (writes <level>-by-topic.md). "
                           "Default: chapter-wise, chapters auto-detected (<level>.md)")
    c.set_defaults(func=cmd_convert)

    i = sub.add_parser("inbox", help="convert every chapter in data/inbox")
    i.add_argument("--level")
    i.add_argument("--backend")
    i.add_argument("--no-review", action="store_true", help="skip the fact-check pass")
    imode = i.add_mutually_exclusive_group()
    imode.add_argument("--per-deck", action="store_true", help="one part per PDF")
    imode.add_argument("--per-slide", action="store_true", help="one section per slide page")
    imode.add_argument("--by-topic", action="store_true", help="small topic sections")
    i.set_defaults(func=cmd_inbox)

    u = sub.add_parser("usage", help="API tokens and spend this month, by backend and job")
    u.add_argument("--month", help="YYYY-MM (default: this month)")
    u.set_defaults(func=cmd_usage)

    w = sub.add_parser("worker", help="web app worker: run queued jobs and Explain requests from Supabase")
    w.add_argument("--backend", help="backend or profile for conversions (default: stages in backends.yaml)")
    w.add_argument("--once", action="store_true", help="process what is queued, then exit")
    w.add_argument("--layout", choices=["topics", "chapters"], default="topics",
                   help="topics (default): all PDFs merged into one flat book of topic sections; "
                        "chapters: auto-detected chapters")
    w.set_defaults(func=cmd_worker)

    s = sub.add_parser("server", help="manage the on-demand vLLM server")
    s.add_argument("action", choices=["setup", "up", "status", "down"])
    s.add_argument("--backend")
    s.set_defaults(func=cmd_server)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
