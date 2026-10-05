"""Compare slide-extraction backends on chosen pages of a deck.

  python eval/bakeoff.py "data/inbox/11 - Paging.pdf" --pages 3,8,30 \
      --backends dashbw_gemma3,dashbw_ministral3,dashbw_qwen35

Writes eval/results/<deck>/<page>.md with every backend's extraction side by side
(plus timing), next to the slide image, for eyeballing.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.llm.client import LLM  # noqa: E402
from backend.paths import PROMPTS_DIR  # noqa: E402
from backend.pipeline.chapter import Chapter, build_slides  # noqa: E402
from backend.pipeline.extract import extract_slide  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--pages", required=True, help="comma list of 1-based pages")
    ap.add_argument("--backends", required=True, help="comma list of backend names")
    args = ap.parse_args()

    pdf = Path(args.pdf)
    slides = {s.page: s for s in build_slides(Chapter(pdf.stem, [pdf]).ingest())}
    pages = [int(p) for p in args.pages.split(",")]
    out_dir = Path(__file__).parent / "results" / pdf.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    prompt = (PROMPTS_DIR / "extract.md").read_text()

    timings: dict[str, list[float]] = {}
    for name in args.backends.split(","):
        llm = LLM(name)
        llm.connect()
        for page in pages:
            t0 = time.time()
            try:
                ex = extract_slide(slides[page], llm, prompt)
                result = ex.model_dump()
            except Exception as e:
                result = {"error": f"{type(e).__name__}: {e}"}
            dt = time.time() - t0
            timings.setdefault(name, []).append(dt)
            (out_dir / f"p{page:03d}.{name}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
            print(f"{name:20} p{page:<3} {dt:6.1f}s  figures={len(result.get('figures', []))}")

    for page in pages:
        shutil.copyfile(slides[page].image_path, out_dir / f"p{page:03d}.png")
        md = [f"# Page {page}", f"![slide](p{page:03d}.png)"]
        for name in timings:
            md.append(f"## {name}\n```json\n{(out_dir / f'p{page:03d}.{name}.json').read_text()}\n```")
        (out_dir / f"p{page:03d}.md").write_text("\n\n".join(md))
    print("\nmean seconds/slide (first call includes model load):")
    for name, ts in timings.items():
        print(f"  {name:20} {sum(ts) / len(ts):.1f}")


if __name__ == "__main__":
    main()
