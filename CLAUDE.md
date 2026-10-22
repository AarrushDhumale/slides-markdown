# CLAUDE.md: slides → markdown study docs

Converts college lecture slide PDFs into readable Markdown study notes, using a self-hosted vision LLM. The user is a student. This file records the state of the project, the decisions behind it, and the traps already hit, so a new session can continue without re-learning them. `README.md` has the user-facing usage; keep both in sync when behaviour changes.

**Git:** `main` includes the web app, hosted-model layer and topic book (merged Oct 2026). The docs-style frontend redesign is committed on branch `frontend-redesign` (not merged to `main` yet). Commit only when the user asks.

**Resuming?** Read `docs/SESSION_LOG.md` first: current state, what was measured and decided, what is unverified, and the open work list.

## How the user runs it
```bash
cd /home/aarrush/personal/projects/slides_to_markdown
.venv/bin/s2m convert data/inbox/toc/*.pdf --chapter "toc" --level learner,summary
.venv/bin/s2m convert "data/inbox/OS Slides Combined 1-12.pdf" --chapter "os-midsem" --level learner,summary
.venv/bin/pytest -q          # offline tests (synthetic PDFs + fake LLM + in-memory Supabase, no GPU/network)
.venv/bin/s2m usage          # API spend this month (data/usage.jsonl)
.venv/bin/s2m worker         # web app worker (needs SUPABASE_URL / SUPABASE_SERVICE_KEY in .env)
cd web && corepack pnpm@9.15.4 dev|build|typecheck|test   # Next.js app (no npm on this machine)
```
- **Levels:** `proficient` (dense revision), `learner` (main doc), `donkey` (ELI5), `notes` (short lecture notes with key diagrams, condensed *from the learner doc*), `summary` (cram sheet, condensed *from the learner doc*), `all`. `dockey` is still accepted as an alias for `donkey`.
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
7. **`summarize.py`** condenses each learner section into the `summary` (`prompts/summary.md`, plus chapter takeaways) or the `notes` (`prompts/notes.md`, keeps up to 3 diagrams) level.

Caches of generated prose are keyed by the full prompt, the input and `llm.generation_id` (model plus sampling options). Prompt edits therefore invalidate them automatically. They live in `data/chapters/<slug>/.cache/` (`sections*/`, `outline-*.json`, `overlap-*.json`, `chapters.json`, `papers-map-*.json`) via `chapter.cache_root()`; `Chapter.ingest()` moves caches left in the old layout there (`migrate_cache`).

8. **`papers.py`** (optional, when `Chapter.papers` is set: `--papers`, or `data/inbox/<chapter>/papers/*.pdf`) reads past-year papers with the vision reader (cached per page under the PDF's cache dir), joins questions split across pages, pairs solution PDFs by filename, maps each question to outline sections with one cached text call per paper, and writes `papers.json` + `papers.md`. `assemble(..., exam_tags, stars)` adds the "Asked in" line and ★ at assembly time only, so prose caches are unaffected. No model writes solutions. Web app integration is not built yet.

## Web app (`web/`, `backend/worker.py`, `supabase/migrations/001_init.sql`)
- **Shape:** Vercel (Next.js 15, Tailwind v4) ↔ Supabase (Postgres + Storage + Realtime) ↔ `s2m worker` (laptop now,
  Cloud Run later). Vercel never runs the pipeline; the worker only makes outbound calls and *pulls* jobs.
- **Auth:** anonymous Supabase users (no visible sign-in, user's choice for phase 1); RLS per owner. Writes that need checks
  (notebooks, uploads, jobs, explain) go through API routes with the service-role key; quotas in `web/lib/limits.ts`.
- **One flat book per notebook (user's later instruction, Oct 2026):** the worker runs `convert_chapter(by_topic=True)`
  (`s2m worker --layout topics`, the default): all PDFs merged, **no chapters**, sections by topic, shown as a book
  (`web/lib/book.ts`, `web/components/reader.tsx`: cover + contents page, one page per topic, prev/next, ← →, scroll
  toggle). `run.TOPIC_LAYOUT` = `topics.md` prompt, ~5 slides/section, ceiling 80 sections (chapter outlines cap at
  12), and `outline.fallback_outline` (consecutive slides grouped, never across PDFs) if the outline call fails. This
  supersedes "don't fragment into many small sections" **for the web app only**; the CLI default is still chapter-wise.
- **Layout the user asked for:** two panes (sources + Studio action cards left, reader right); Summary is the default view;
  Learner / Revision (proficient) / Explain simply (donkey) / Quiz are buttons. Quiz = `quiz.json` parsed from the learner
  doc (`backend/pipeline/quiz.py`), no model call. Slide chips (from `<!-- slides … -->` anchors) open a popover.
- **Explain this:** highlight text or click a figure/slide → `/api/explain` → cache hit, or an `explain_requests` row the
  worker answers with the local model (streams `partial_text`), or a direct Gemini stream when the worker is offline/busy.
  Context is built server-side (`backend/pipeline/explain.py`, mirrored in `web/lib/explain-context.ts`); section index =
  i-th slide anchor on both sides. `web/lib/explain-prompt.generated.ts` must match `backend/prompts/explain.md`
  (a test checks; `pnpm sync-prompt`).
- **Worker files:** `data/web/notebooks/<id>/{pdfs,chapter}`; uploads `outputs/<id>/{<level>.md, assets/, slides/*.webp,
  slides.json, extracts.json, quiz.json}`. `convert_chapter` now also writes `extracts.json` + `slides.json` into the chapter dir.

## Frontend look (Oct 2026, `frontend-redesign`)
Docs-style dark reader (Vercel/Stripe/Clerk docs): flat, no gradients or heavy animation. Themes `docs` (dark) and `paper` (light) via `data-theme`; reading fonts `inter|atkinson|lora|literata|nunito` via `data-font`; both picked from the header palette icon and also settable with `?theme=` / `?font=`. Reader = topics nav | content | "On this page". **Demo without Supabase:** `/notebooks/demo?view=learner&p=1` (static files in `web/public/demo/`). Tabbed code needs fences with `tab="Label"`. Full detail and open work: `docs/SESSION_LOG.md` section 11.

## Infrastructure and models
- **Default (since Oct 2026): hosted APIs, two models per run, both on OpenRouter.** `stages:` in `backends.yaml` point at
  `chain` backends: extract = Qwen3.5-35B (OpenRouter, `extract_chain_qwen`) → Gemini 2.5 Flash → `ollama_local`; write =
  DeepSeek V3.2 (text-only) → Qwen3.5 → `ollama_local`; explain = `ollama_local` → Gemini 3.1 Flash-Lite. Chains fail over
  stickily (per run) when a member gives up; `model_id`/`generation_id` follow the active member so caches stay keyed by
  the model that wrote them. Chosen from a live bake-off on the user's slides (`docs/MODEL_COMPARISON.md`): reader accuracy
  parity with Gemini at ~half the cost ($0.08 vs $0.16 per 100 slides); DeepSeek wrote cleaner notes than Qwen. Measured
  total ≈ $0.19 per 100 slides (learner + summary). Profiles: `qwen_deepseek` (= default), `api_pair` (Gemini 2.5 Flash
  reads), `dev_free`, `local`, DashBW ones. `eval/bakeoff.py` has `--stage extract|write` to re-measure.
- **Spend caps:** every call is logged to `data/usage.jsonl`; `S2M_MAX_USD_PER_JOB` / `S2M_MAX_USD_PER_MONTH` raise
  `BudgetExceeded`, which the pipeline's broad `except Exception` handlers re-raise (never degrade around a cap).
- **`kind: openai` backends** now get `options:` (presence_penalty etc.; `repeat_penalty`→`repetition_penalty` via
  extra_body), our own retry/backoff (SDK retries off), `rpm` pacing, `price`, `free`, `response_format`, and image
  stripping when `vision: false`. Model ids for Gemini 3.x were not verified against the live API yet.
- **OpenRouter traps (found Oct 2026 with a live key):** never send `json_schema` to Qwen there (`response_format:
  json_object`; the schema is already in the prompt); pin fp8 hosts with `provider.order/quantizations` because default
  routing picked the dearest host and some hosts are fp4; price per slide must come from the usage ledger, not the
  listing. `extract_slide` rejects a valid-but-empty reading (`is_empty_reading`); `sanitize` drops code lines echoed as
  bullets (`drop_code_duplicates`). The OpenRouter key lives in `.env` (gitignored); never put keys in `.env.example`.
- **Laptop model:** `ollama_local` = `qwen3.5:latest` (9.7B, vision, Q4); same presence_penalty override and `think: false`.
  The 35B doesn't fit (8 GB VRAM, 14 GB RAM).
- **Machines:**
  - **DashBW** (`ssh DashBW`): 2× RTX PRO 6000 96 GB, shared. Runs everything through its shared **Ollama** daemon, reached via an ssh tunnel to local port 11435.
  - **Dash5000** (`ssh Dash5000`): RTX 5000 32 GB.
  - **Laptop:** RTX 5060 8 GB.
- **DashBW model:** `qwen3.5:35b-a3b` (36B MoE, ~3B active, Q4_K_M, vision, 256K context), `--backend dashbw_qwen35`.
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
- **Shorter Learner docs.** The current budgets are `LENGTH_FACTOR = {"proficient": 0.6, "learner": 1.1, "donkey": 1.3}` with a 900-word cap and the tighten pass. They said "keep the shortened length".
- **No hallucinated extras.** Material not on the slides goes in `> **Beyond the slides:**`. Don't decode raw dumps (DWARF, hex) beyond what the slides annotate.
- **Feedback is blunt and terse.** When a request is ambiguous ("revert", "fix issues"), ask a targeted question or reproduce the problem before changing code.

## Gotchas for agents
- The system `timeout` is **uutils** and failed to execute the relative path `.venv/bin/s2m`. Use the absolute path to `s2m`.
- `pkill -f "<pattern>"` matches its own shell when the pattern is in the command line. Use `ps -eo pid,args | grep "[b]in/s2m"` and kill by PID.
- Foreground `sleep` is blocked in this harness. Run long jobs with `run_in_background` and wait for the notification.
- Big decks take 5–15 minutes. Most reruns are fast because readings and sections are cached.
- Node here has no npm and no built-in TypeScript: use `corepack pnpm@9.15.4`, and `tsx` for web tests.
- Supabase CLI isn't installed; a session can download it to its scratchpad (`supabase start` uses `supabase/config.toml`).

## Known limitations / next steps
- **Not built yet:** Google sign-in (link to the anonymous user), the Cloud Run worker image and durable cache mirroring
  to Storage, a slide pane synced to scroll, "retry failed slides" (re-queueing the job does it: finished work is cached).
- **Accuracy gaps:**
  - Slides that are raw listings (assembly, DWARF) still produce guessy explanations.
  - Topic-overlap detection between decks relies on the model; the writer sometimes ignores "don't re-explain" instructions, which is why pure repeats became deterministic recaps.
  - Single-part chapter summaries can lose their slide-level detail.
- **Possible improvements:** try `think: true` for writing (cleaner prose, slower), and a per-chapter progress display in the CLI.
