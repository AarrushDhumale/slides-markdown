# CLAUDE.md: slides → markdown study docs

Converts college lecture slide PDFs into readable Markdown study notes, using a self-hosted vision LLM. The user is a student. This file records the state of the project, the decisions behind it, and the traps already hit, so a new session can continue without re-learning them. `README.md` has the user-facing usage; keep both in sync when behaviour changes.

**No git repo.** There is no history to revert to, so "undo" means editing by hand. Before making large changes, offer to `git init` and commit.

## How the user runs it
```bash
cd /home/aarrush/personal/projects/slides_to_markdown
.venv/bin/s2m convert data/inbox/toc/*.pdf --chapter "toc" --level learner,summary
.venv/bin/s2m convert "data/inbox/OS Slides Combined 1-12.pdf" --chapter "os-midsem" --level learner,summary
.venv/bin/pytest -q          # 43 offline tests (synthetic PDFs + fake LLM, no GPU)
```
- **Levels:** `proficient` (dense revision), `learner` (main doc), `dockey` (ELI5), `summary` (condensed *from the learner doc*), `all`.
- **Layout flags:**
  - default is **chapter-wise**, written to `<level>.md`;
  - `--by-topic` → `<level>-by-topic.md`;
  - `--per-deck` → `-by-deck`;
  - `--per-slide` → `-by-slide`.
- **Other flags:** `--no-review` skips the fact-check pass. `--backend NAME|PROFILE` overrides the model.
- **Folders:** inputs go in `data/inbox/` (a subfolder is one multi-PDF chapter). Outputs go to `data/chapters/<slug>/` with `assets/`. Slide readings are cached in `data/cache/<pdf-sha>/` and survive deleting chapter folders.
- **The user regenerates docs themselves.** Don't regenerate their decks unless asked. Never write into `data/chapters/` for experiments. `Chapter(...).ingest()` writes a manifest there, so use a scratch dir and a `SimpleNamespace(dir=...)` stand-in for the chapter.

## Pipeline (`backend/pipeline/`, orchestrated by `run.py:convert_chapter`)
1. **`ingest.py`** turns each PDF into page PNGs, the text layer and figure crops. It's cached per PDF sha; bump `INGEST_VERSION` if this logic changes.
   - **Candidates and template removal:** figure candidates are raster images plus vector clusters (`cluster_drawings`). Repeated template elements (banners, logos, rules on ≥40% of pages) are removed *before* merging.
   - **Rendering:** crops are rendered from a scratch copy of the page. Template images are deleted *by xref* (blanking pixels damaged figures underneath). Body text crossing a figure, or lying in a picture's margin, is redacted, then whitespace is trimmed.
   - **Identity:** crops carry a dHash plus embedded-image digests, so the same figure on several slides is detected.
2. **`chapter.py`** assigns slide ids `A·12` (PDF letter·page). It also detects exact duplicate slides, conservative build-up slides, and repeated figures (`canonical`).
3. **`extract.py`** sends the slide image, its crops and the text-layer hint to the VLM and gets `SlideExtract` JSON. That covers bullets, LaTeX, code, tables and figures, each figure with importance and its crop id.
   - Results are cached per (pdf, page, model, `EXTRACT_VERSION`).
   - `sanitize()` fixes model quirks.
   - A slide that fails falls back to its text layer and isn't cached. The run aborts if more than 30% of slides fail.
4. **Layout:**
   - **Default:** `chapterize.py` detects the course's chapters with one model call, then `normalise()` cleans them up:
     - contiguous and ordered;
     - starts snapped back to title/agenda slides;
     - same-titled neighbours merged;
     - tiny chapters merged with *combined titles*;
     - 14 or fewer teaching slides means one chapter with no call.

     `per_deck.build_grouped_outline` then splits each chapter into parts of about 10 slides via `outline.build_outline`. Overlap with earlier chapters is resolved before writing:
     - bullet-level matches;
     - a semantic `topic_overlaps` call per group;
     - "same / nothing new" sections become verbatim **recap** sections with no model rewrite.
   - **Other modes:** `outline.py` (topic mode), `per_deck.py` and `per_slide.py`.
5. **`write.py`** writes each section with level prompts (`prompts/common.md` + `<level>.md` + mode extras), then fact-checks it (`prompts/review.md`). The fact-check output is rejected if it's gutted or degenerate. Guards, in order:
   1. degeneration check → regenerate from scratch → strip bad paragraphs;
   2. `missing_slides` coverage check → one retry listing the skipped slides;
   3. overshoot more than 1.35× the word budget → one `tighten` pass.
6. **`assemble.py`** normalises Markdown (math, fences, details, slide-ref stripping, alt text), guarantees headings, inserts missing essential figures, turns repeat images into links and builds the contents list. Chapter mode lists chapters only, strips quizzes from non-final parts, and drops the duplicate heading on single-part chapters.
7. **`summarize.py`** condenses each learner section (`prompts/summary.md`) and adds chapter takeaways.

Caches of generated prose are keyed by the full prompt, the input and `llm.generation_id` (model plus sampling options). Prompt edits therefore invalidate them automatically. They live in `sections*/`, `outline-*.json`, `overlap-*.json` and `chapters.json`.

## Infrastructure and models
- **Machines:**
  - **DashBW** (`ssh DashBW`): 2× RTX PRO 6000 96 GB, shared. Runs everything through its shared **Ollama** daemon, reached via an ssh tunnel to local port 11435.
  - **Dash5000** (`ssh Dash5000`): RTX 5000 32 GB.
  - **Laptop:** RTX 5060 8 GB.
- **Default model:** `qwen3.5:35b-a3b` (36B MoE, ~3B active, Q4_K_M, vision, 256K context) for all stages. Config is in `backend/llm/backends.yaml`.
  - It beat gemma3:12b, ministral-3:8b and minicpm-v on dense tables and diagrams (`eval/bakeoff.py`).
  - Fallback profile `dashbw_small`: ministral-3 to read slides, qwen3:14b to write.
- **Critical sampling setting.** Qwen3.5's Ollama defaults include `presence_penalty 1.5`, which made long sections collapse into word salad. `backends.yaml` overrides it (`presence_penalty 0, repeat_penalty 1.05`). Keep it.
- **Ollama doesn't enforce JSON schemas for this model**, so fields go missing, ids come back as objects, and strings are left unterminated. Mitigations already in place:
  - the schema is spelled out in the prompt (`with_schema_instructions`);
  - list fields default to `[]`;
  - id coercion validators;
  - `parse_json_reply`: lenient parse, then `close_unterminated_strings`, then `json-repair`.
- **Thinking off (`think: false`).** The model then "thinks out loud" in its answers ("Wait, let's re-check…"). `degeneration()` detects this. Turning thinking on is an untested alternative.
- **Campus network.** The Sophos firewall intercepts HTTPS, and Dash5000 trusts its CA but DashBW doesn't. Downloads need a campnet captive-portal login, which expires; the keep-alive returns `live_off`, so it can't extend the session. `scripts/campnet_login.sh` is the terminal login, and the user must run it themselves (it takes their credentials). Inference doesn't need internet.
- **Unused:** the vLLM backends and the `s2m server` relay machinery (`llm/remote.py`) were built for Qwen3.5 via vLLM but never used, because the Ollama pull was simpler.

## User preferences (learned the hard way)
- **Chapter-wise layout.** Chapters are auto-detected, and the main doc must **not** be fragmented into many small sections. A single-topic upload stays whole. "n slides → n sections" meant decks, not pages.
- **Shorter Learner docs.** The current budgets are `LENGTH_FACTOR = {"proficient": 0.6, "learner": 1.1, "dockey": 1.3}` with a 900-word cap and the tighten pass. They said "keep the shortened length".
- **No hallucinated extras.** Material not on the slides goes in `> **Beyond the slides:**`. Don't decode raw dumps (DWARF, hex) beyond what the slides annotate.
- **Feedback is blunt and terse.** When a request is ambiguous ("revert", "fix issues"), ask a targeted question or reproduce the problem before changing code.

## Gotchas for agents
- The system `timeout` is **uutils** and failed to execute the relative path `.venv/bin/s2m`. Use the absolute path to `s2m`.
- `pkill -f "<pattern>"` matches its own shell when the pattern is in the command line. Use `ps -eo pid,args | grep "[b]in/s2m"` and kill by PID.
- Foreground `sleep` is blocked in this harness. Run long jobs with `run_in_background` and wait for the notification.
- Big decks take 5–15 minutes. Most reruns are fast because readings and sections are cached.

## Known limitations / next steps
- **Not built yet:**
  - the planned **web viewer** (FastAPI + React, KaTeX, slide side panel);
  - **highlight → "explain this better"** (laptop Ollama by default).
- **Accuracy gaps:**
  - Slides that are raw listings (assembly, DWARF) still produce guessy explanations.
  - Topic-overlap detection between decks relies on the model; the writer sometimes ignores "don't re-explain" instructions, which is why pure repeats became deterministic recaps.
  - Single-part chapter summaries can lose their slide-level detail.
- **Possible improvements:** try `think: true` for writing (cleaner prose, slower), and a per-chapter progress display in the CLI.
