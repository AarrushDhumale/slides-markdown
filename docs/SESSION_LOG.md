# Session log: web app, hosted models, topic book, model comparison (Oct 2026)

A handoff for starting a fresh session without re-deriving any of this. Read in this order: **`CLAUDE.md`** (auto-loaded; working rules and traps) → this file (what happened, current state, what's open) → `docs/PROJECT_GUIDE.md` (architecture and why each framework) → `docs/MODEL_COMPARISON.md` (measured model results).

## 1. State right now

| | |
|---|---|
| Branch | `web-app` (not merged, not pushed). `main` is still the original commit `f441a43`. |
| Commits on `web-app` | `5e55c5d` web app + hosted-API model layer + "Explain this" · `f7c1242` project guide · `288e193` topic book, book reader, measured model comparison |
| Uncommitted | `.env.example`: your fix of a stray word typed in front of `GEMINI_API_KEY` (the committed copy has the typo). `.vscode/` is the editor's folder, never committed. |
| Running | Nothing. Local Supabase (Docker), dev server and worker are all stopped. |
| Tests | `.venv/bin/pytest -q`: **77 pass** · `cd web && corepack pnpm@9.15.4 test`: **10 pass** · typecheck and `next build` pass |
| Secrets | `OPENROUTER_API_KEY` is in `.env` (gitignored, mode 600). It was once pasted into the tracked `.env.example`, was moved, and was never committed. Spend so far: **$0.088 of the key's $10 limit**. No Gemini key exists. Keys never go in `.env.example`. |

## 2. What was asked, in order, and what was built

1. **"Publish this on Vercel, NotebookLM-style, page-by-page notes from the local model"** → planned (three rounds). The user's decisions: no sign-in in phase 1 (anonymous sessions), two panes only, study tools as buttons (Summary default, Learner, Revision, Explain simply, Quiz), shortest summary first, then a switch to hosted API models (volume is ~10 decks a month at most, often none), two models (a reader and a writer), and an "Explain this" assistant on the local model.
2. **Built (`5e55c5d`).** Python: `backend/llm/{client,usage}.py` (fallback chains, retries, rate limits, spend caps, usage ledger), `backend/worker.py`, `pipeline/{quiz,explain}.py`, `prompts/explain.md`. Database: `supabase/migrations/001_init.sql` (RLS, `claim_job`, storage, realtime). Web: `web/` (Next.js 15, React 19, Tailwind v4, Radix, Supabase, KaTeX, react-markdown).
3. **Verified live** on a local Supabase with the laptop model: upload → job → summary + learner + quiz; "Explain this" streaming for text and figures; on-demand Revision job; ownership/quota/RLS checks; light, dark and phone screenshots.
4. **"Log the whole project and explain every framework"** → `docs/PROJECT_GUIDE.md` (`f7c1242`).
5. **"Combine all PDFs into one doc split by topic, shown like a book; compare Qwen3.5-35B reader + DeepSeek V3.2 writer with the current setup"** → built and measured (`288e193`), details below.

## 3. The topic book (the current web layout)

- The worker runs `convert_chapter(by_topic=True)` by default (`s2m worker --layout topics|chapters`). All PDFs of a notebook become **one flat document, no chapters**. The CLI default is still chapter-wise.
- `run.TOPIC_LAYOUT`: `prompts/topics.md`, ~5 slides per section, ceiling of 80 sections (chapter outlines cap at 12), and `outline.fallback_outline` (consecutive slides, never across PDFs) if the outline call fails.
- Summaries keep the learner section's heading (`summarize.keep_heading`) so a topic has one title at every level.
- Reader (`web/components/reader.tsx`, `web/lib/book.ts`): cover with overview, takeaways and numbered contents → one page per topic, Previous/Next cards, ← → keys, progress bar, read-checkmarks, place remembered per notebook, Scroll toggle, print prints everything. A section index is "the i-th `<!-- slides … -->` anchor", the same in Python and TypeScript (the Explain context depends on it).
- Checked live on two PDFs (Paging + Demand Paging, 12 slides, laptop model): a flat book of 2 topics with textbook-style titles.

## 4. The model comparison (measured on the user's slides, via OpenRouter)

Nine hard pages from three decks for readers; three sections of the Paging deck for writers; one run each. Full tables: `docs/MODEL_COMPARISON.md`. Raw outputs to read yourself: `eval/results/` (gitignored; per-page `.md` files plus `write/<writer>.md` and `write/summary.md`).

- **Readers:** Qwen3.5-35B vs Gemini 2.5 Flash are about equally accurate. Each made one real figure error (Qwen: PTE bit positions off by one on Paging p7; Gemini: dropped 2 of 16 table rows and cited frame numbers not on the slide, p12). Cost per 100 slides: **Qwen $0.08, Gemini $0.16**.
- **Writers:** DeepSeek V3.2 wrote cleaner, better-structured notes and cost ~30% less, but ran ~3× slower (166 s vs 51 s for 3 sections). Qwen copied the reader's wrong bit numbers into a quiz answer and leaked a long figure description. DeepSeek stated a wrong U/S polarity that isn't on the slide. Both overshoot the length budget (~140 words/slide vs ~100 in the user's accepted docs).
- **Default switched to Qwen reads + DeepSeek writes** (profile `qwen_deepseek`, ≈ **$0.19 per 100 slides**; Gemini-first is profile `api_pair`, ≈ $0.27). The user has only an OpenRouter key, which also made this the practical default. To revert: `--backend api_pair` or `stages.extract` in `backend/llm/backends.yaml`.
- **Blind spot:** the fact-check pass compares the notes with the reader's extraction, not the slide image, so any reading error passes straight through.

## 5. Problems found by measuring (all fixed; more detail in the guide's traps list)

1. `response_format: json_schema` made Qwen answer a dense slide with empty arrays (35 tokens) on OpenRouter's default host → Qwen now uses `json_object`, and `extract_slide` rejects a valid-but-empty reading of a slide that has text (`is_empty_reading`; never cached).
2. OpenRouter's headline price is the cheapest host's and default routing isn't price-first (Qwen went to a $0.24/$1.80 host, not $0.15/$1.00; some hosts are fp4) → hosts pinned in `backends.yaml` (`provider.order/quantizations/require_parameters`).
3. GMICloud (the "cheapest" DeepSeek host) ignored `reasoning: {enabled: false}` (~1,400 hidden tokens per short answer, 3× slower) → DeepSeek pinned to AtlasCloud, Baidu.
4. My bake-off harness reused job ids so each run's cost included earlier runs → fixed, regression test added.
5. The 9B laptop model can loop on a dense slide for minutes → `max_output: 4096` on `ollama_local`.
6. Qwen echoed code lines as bullets and tagged inline words as code → `extract.drop_code_duplicates`.
7. A test run had cached an empty Qwen reading in `data/cache`; it was deleted, and the rest ran in a scratch data folder.

## 6. Not verified / open

**Not verified**
- Anything through the direct Gemini API (no key). Its model names are unconfirmed there (`google/gemini-2.5-flash` exists on OpenRouter; `gemini-3.1-flash-lite` direct is untested), and the cloud fallback for "Explain this" has never run live.
- A hosted (not local) Supabase and a Vercel deployment; the Cloud Run worker image does not exist yet.
- The web app has only been run end to end on the laptop model, not with the hosted default models.
- A full 250-slide book; the summary step's cost (estimated, ~+$0.015 per 100 slides).
- The `ollama_local` outline for long books may truncate (4,096-token cap); `fallback_outline` is the safety net.

**Open work, roughly in priority order**
1. Close the reading-error blind spot: stop the writer repeating exact numeric or positional detail from a figure description unless it's in the slide text, and/or cross-read number-heavy slides with a second model and flag disagreements.
2. Both writers overshoot the length budget even after the tighten pass (the user prefers short notes).
3. Deployment: hosted Supabase (run `001_init.sql`, enable anonymous sign-ins), Vercel (root dir `web`), a worker host; later Google sign-in linked to the anonymous user, durable cache mirroring to Storage, a slide pane that follows scrolling.
4. Decide whether to merge/push `web-app`; commit the `.env.example` fix.

## 7. How to resume

```bash
cd /home/aarrush/personal/projects/slides_to_markdown
.venv/bin/pytest -q                                  # 77 tests, offline
.venv/bin/s2m usage                                  # API spend (data/usage.jsonl)
.venv/bin/s2m convert deck.pdf --by-topic --level learner,summary     # CLI, topic book
cd web && corepack pnpm@9.15.4 dev                   # needs web/.env.local (see web/.env.example)
```
- Local stack: download the Supabase CLI into the session scratchpad (`curl -sSL -o s.tgz https://github.com/supabase/cli/releases/latest/download/supabase_linux_amd64.tar.gz`), then `supabase start` from the repo root. `supabase/config.toml` already enables anonymous sign-ins and disables studio, mail and analytics (the mail port clashed).
- Worker against it: `S2M_DATA=<scratch> SUPABASE_URL=… SUPABASE_SERVICE_KEY=… .venv/bin/s2m worker --backend local` (laptop model) or without `--backend` (hosted models, needs the OpenRouter key).
- Re-run the comparisons: the three `bakeoff.py` commands at the bottom of `docs/MODEL_COMPARISON.md`. Use a scratch `S2M_DATA` (copy a deck's `data/cache/<sha>/{pages,figures,meta.json}` into it) so `data/cache` isn't added to.
- Browser checks: Playwright was used with `playwright-core` and `/usr/bin/google-chrome`, installed per session in the scratchpad (nothing in the repo).

## 8. Environment gotchas (also in `CLAUDE.md`)
- No npm here: use `corepack pnpm@9.15.4`; Node has no built-in TypeScript, so web tests use `tsx`.
- Foreground `sleep` is blocked: use `run_in_background` and a Monitor. `kill` by the Python PID: a `pkill`/`grep` pattern also matches the wrapping `bash -c` (that is why a worker once looked like it "exited 1"; run directly, it exits 0).
- The scratchpad is per session and starts empty.
- Laptop: RTX 5060 (8 GB), `qwen3.5:latest` 9.7B (vision). The 35B model doesn't fit. Local slides take ~50 s each to read.
- `grep -r` from the repo root gave a false "no matches" once; check for secrets with `git ls-files -co --exclude-standard -z | xargs -0 grep -l …`.

## 9. User preferences seen this session
Terse, blunt feedback; wants a plan before big builds; wants things robust and costs compared with real tables; no sign-in yet; two panes; book-style reading; chapter-wise stays the CLI default; commit only when asked (`CLAUDE.md`); "Beyond the slides" for anything not on a slide. Earlier plan files exist at `~/.claude/plans/` but are superseded by `docs/PROJECT_GUIDE.md`.
