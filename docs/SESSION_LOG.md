# Session log: web app, hosted models, topic book, model comparison, Notes level, past papers (Oct 2026)

A handoff for starting a fresh session without re-deriving any of this. Read in this order: **`CLAUDE.md`** (auto-loaded; working rules and traps) → this file (what happened, current state, what's open) → `docs/PROJECT_GUIDE.md` (architecture and why each framework) → `docs/MODEL_COMPARISON.md` (measured model results).

## 1. State right now

| | |
|---|---|
| Branch | **Session 3 (Oct 19-20): `main` now equals `web-app`** (merged and pushed). Work continues on branch **`frontend-redesign`** (created from `main`, not pushed). See section 11. |
| Commits on `web-app` | `5e55c5d` web app + hosted-API model layer + "Explain this" · `f7c1242` project guide · `288e193` topic book, book reader, measured model comparison |
| Uncommitted | **Session 3's frontend redesign** (section 11). Session 2's work below was committed as `a8ad2ca`. Older note: everything from session 2 (section 10 below): Notes level, dockey→donkey, `.cache/` layout, past papers, bake-off images, migration 002, plus the `.env.example` typo fix. `.vscode/` is the editor's folder, never committed. Commit only when asked. |
| Running | Nothing. Local Supabase (Docker), dev server and worker are all stopped. |
| Tests | `.venv/bin/pytest -q`: **84 pass** · `cd web && corepack pnpm@9.15.4 test`: **10 pass** · typecheck and `next build` pass |
| Secrets | `OPENROUTER_API_KEY` is in `.env` (gitignored, mode 600). It was once pasted into the tracked `.env.example`, was moved, and was never committed. Spend so far: about **$0.17 of the key's $10 cap**, but the OpenRouter account has **$0 purchased credits** (free tier), so bursts of requests fail with 402 `in_flight_budget_exhausted` (see section 10). No Gemini key exists. Keys never go in `.env.example`. |

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
4. Decide whether to merge/push `web-app`; commit the pending work (section 10) and the `.env.example` fix.
5. **Frontend next** (user's stated next step): see "Starting the frontend next session" in section 10. Also pending: add OpenRouter credits and re-run the bigger bake-offs; try past papers on real PDFs; show the past-paper analysis in the web app.

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

## 10. Session 2 (Oct 17-19, 2026): what changed, what was measured, where to start the frontend

**Asked, in order:** images in the bake-off outputs; a Notes section; whether past papers help (then: build it, as a CLI content analysis before any web work); Dockey→Donkey; clean up junk Markdown; then re-run the writer bake-off and test on bigger/other PDFs.

**Built (all uncommitted on `web-app`)**
- **Bake-off images** (`eval/bakeoff.py`): `render_section` runs the real-doc clean-up, copies crops to `write/assets/`, drops broken links and adds a collapsed "Source slides" block (`write/slides/`). New `--spread` samples sections evenly across a deck. Tested offline.
- **Notes level:** `notes` = short lecture notes condensed from the learner doc (`prompts/notes.md`, learner words ÷ 4 clamped 80-400, up to 3 diagrams, no takeaways). `summarize.py` now has `STYLES` (`summary`, `notes`); `run.CONDENSED` = levels that need the learner sections. Web: "Lecture notes" card (`studio.tsx`), label in `reader.tsx`, `limits.ts`, jobs route, `notebook-view.tsx`.
- **Dockey → Donkey:** renamed everywhere (prompt, level key, labels, tests, docs); `s2m ... --level dockey` still works as an alias. The web button text is still "Explain simply". `data/chapters/paging/dockey.md` was renamed to `donkey.md`.
- **Migration `supabase/migrations/002_notes_donkey.sql`:** widens `documents.level` (adds `notes`, `donkey`), renames existing `dockey` rows and `jobs.levels`. **Not applied anywhere yet** (the local Supabase is stopped, no hosted project).
- **Cache layout:** generated-prose caches now live in a hidden `data/chapters/<slug>/.cache/` (`chapter.cache_root()`, `migrate_cache()` runs from `Chapter.ingest()`). All existing chapters were migrated, nothing regenerated. Chapter folders now show only docs + `assets/` + `chapter.json` (+ `extracts.json`/`slides.json` which the worker reads).
- **Deleted at the user's request:** `data/chapters/os-combined`, `os-folder`, `11-paging` (same PDF as `paging`), and the old `eval/results/11 - Paging/write/`.
- **Past papers (`backend/pipeline/papers.py`, `prompts/paper.md`, `paper_map.md`; CLI only):**
  - Input: `data/inbox/<chapter>/papers/*.pdf` or `s2m convert ... --papers a.pdf b.pdf`. Typed or scanned. Files named `solution|soln|answer|key` are paired with the matching paper; **no model writes solutions**.
  - Reads each page with the vision reader (cached under the PDF's cache dir), joins questions split across pages, maps each question to outline sections (one cached text call per paper, `.cache/papers-map-*.json`), then writes `papers.json` + `papers.md` (topics ranked by marks, question types, repeats, near-duplicates, questions the slides don't cover, every question with its official solution).
  - Every doc level gets `> **Asked in:** Midsem 2023 Q4 (8 m) · ...` under asked topics, and a ★ on the top quarter by marks. Added at assembly only (`assemble(..., exam_tags, stars)`), so prose caches are untouched.
  - Verified **only with synthetic papers and a fake model**. No real paper has been read yet; the user will add PDFs to `data/inbox/<chapter>/papers/`.

**Measured (writer bake-off, DeepSeek V3.2 vs Qwen3.5-35B, reader held fixed; tiny samples)**
- *11 - Paging* (3 of 8 sections): DeepSeek 902 words vs 640 budget, $0.0015, 45 s; Qwen 1130 vs 640, $0.0088, 56 s. Qwen copies the reader's figure descriptions into the prose, merged the Valid and Present bits, and left one section without a quiz. DeepSeek flagged its one outside-the-slides remark correctly.
- *12 - Demand Paging* (4 sections): DeepSeek finished 4/4 (1213 vs 880 words, $0.0068, 180 s); Qwen finished 2/4 (402 on the other two). On the two shared sections the length overshoot was similar (1.4-1.65x) and Qwen again narrated a figure walkthrough; DeepSeek's quiz covered all three fault cases.
- **Both writers overshoot the word budget on every deck tested (1.2-2.4x, quiz and answers included)**; the tighten pass does not fix it. This is open item 2 below.
- Conclusion so far: DeepSeek stays the default writer. Accuracy was judged by reading notes against the reader's extracts, **not against the slide images**.

**Blocked: OpenRouter credits.** The account shows `total_credits: 0` (free tier); the key's $10 limit is only a cap. Requests fail with 402 `in_flight_budget_exhausted` when several are in flight (the 287-slide OS run lost 228/278 reads; `lecture-13_Class` fully failed). About 50 of those readings are cached for the OpenRouter reader. **Next step: add credits at openrouter.ai/settings/credits** (about $5), or lower `max_concurrency` to 1 in `backends.yaml`. Then re-run: `.venv/bin/python eval/bakeoff.py "data/inbox/OS Slides Combined 1-12.pdf" --stage write --reader openrouter_qwen35 --writers deepseek_v32,openrouter_qwen35 --sections 12 --spread` (about $0.55) and the `lecture-13_Class` deck. Results go to `eval/results/<deck>/write/`.

### Starting the frontend next session
The user wants to work on the frontend (`web/`) next. Read `CLAUDE.md` "Web app" first, then this list.
1. **Layout in place:** two panes (sources + Studio cards left, reader right); Summary default; cards in `web/components/studio.tsx` (`ACTIONS`: Summary, Lecture notes, Learner notes, Revision sheet, Explain simply, Quiz). The grid is `grid-cols-2` with Summary spanning two columns, so with Notes there are 7 cells and Quiz sits alone on the last row. Needs a layout decision.
2. **Naming:** the Donkey level's button still reads "Explain simply"; ask whether it should say "Donkey".
3. **Past papers have no web UI.** The data exists as `papers.json` / `papers.md` in the chapter dir. Ideas the user asked about: an "Asked in" badge/★ in the reader and contents (the tag line is already in the Markdown), a practice mode (question, reveal official solution, link to the topic), a topic-ranking view. Needs: upload slot for papers, worker support (`backend/worker.py` currently never sets `Chapter.papers`; it would also need to upload `papers.json`), a `documents`/Storage entry, and a migration. Decide the data path before building UI.
4. **Apply migration 002** before testing Notes/Donkey in the web app (local Supabase: `supabase start`, then `supabase db reset` or apply the file).
5. **The reader** (`web/components/reader.tsx`, `web/lib/book.ts`) shows Markdown as a book; the Notes docs keep up to 3 diagrams per section, so check how they look in the page view. Slide chips, the Explain popover and quiz extraction (`backend/pipeline/quiz.py` parses the learner doc) are unchanged.
6. Web checks: `cd web && corepack pnpm@9.15.4 typecheck && corepack pnpm@9.15.4 test && corepack pnpm@9.15.4 build` all passed at the end of this session. A live browser check of the new Notes card has **not** been done.

## 11. Session 3 (Oct 19-20, 2026): merge to main, then the frontend redesign

**Git.** Tests first (pytest 84, web typecheck + tests pass). `git merge --ff-only web-app` worked locally, but GitHub's `origin/main` already had PR #1 merged (`7e4ab59`, only up to `ecbfb06`), so the push was rejected. Fix: `git merge origin/main` into local `main` (no conflicts, tree identical to `web-app`), pushed as `c42b725`. Then `git switch -c frontend-redesign`. **Nothing from the redesign is committed yet** (files: `web/app/{globals.css,layout.tsx}`, `web/components/{home-view,logo,markdown,notebook-view,reader,studio,theme-toggle}.tsx`, `web/components/ui/button.tsx`, new `web/lib/remark-codetabs.ts`, new `web/public/demo/`).

**What the user wants.** A simple, calm, dark UI for long reading without fatigue, in the style of Vercel / Stripe / Clerk docs (spec they gave: bg `#121212`, text `#e0e0e0`, muted `#94a3b8`, borders `#27272a`, accent `#38bdf8`, Inter or a screen serif, JetBrains Mono for code, line height ~1.6-1.7, prose 65-75ch, left chapter nav, callouts, images, tabbed code with copy buttons, Previous/Next footer cards). They first showed Spade/LocalCan screenshots (dark, mono eyebrow labels, hairlines); the docs direction replaced that. They also said the bright green was too bright and wanted Notion-calm colours.

**Decisions.** Stayed on Next 15 + Tailwind v4 + Radix + react-markdown. Did **not** adopt Fumadocs/Nextra/MDX: our notes are runtime Markdown from Supabase, not build-time files. Flat style: no shadows, gradients or page-turn animations (`turn-next/prev` removed).

**What exists now (`frontend-redesign`).**
- **Themes** (`data-theme` on `<html>`, set before paint by the script in `layout.tsx`; `?theme=` URL param and `localStorage.palette`): `docs` (default dark) and `paper` (light). Tokens in `globals.css` (`html[data-theme=...]`). Old green accent and the Graphite/Warm night/Dusk palettes were removed.
- **Reading fonts** (`data-font`, `?font=`, `localStorage.font`): `inter` (default), `atkinson`, `lora`, `literata`, `nunito`. `--reading-font` for body, `--heading-font` for headings (Lora keeps Inter headings). UI chrome stays Inter; code is JetBrains Mono. Picker: palette icon in the header (`components/theme-toggle.tsx`, exports `PALETTES`, `FONTS`). The user has not yet chosen a favourite font (I suggested Literata or Atkinson); delete the unchosen ones once they pick.
- **Reader layout** (`components/reader.tsx`): full-width three columns: `DocsNav` (Overview + collapsible Topics, read ticks) | content (`max-w-[44rem]`, centred) | `OnThisPage` (h2/h3 read from the rendered DOM, IntersectionObserver scroll-spy; shown at xl). The sources/tools column (`notebook-view.tsx`) is 280px and can be collapsed with the panel icon. The user asked for it to touch the left edge and run to the right edge, so all max-width caps on the shell were removed.
- **Markdown** (`components/markdown.tsx`): `CodeBlock` (language bar + copy), `CodeTabs`, and `lib/remark-codetabs.ts`. Tabs are **opt-in**: adjacent fences whose meta has `tab="Label"`. The label currently falls back to the language name (the meta is lost after rehype-raw). The generator prompts do not emit `tab=` yet. Callouts are the existing blockquotes (`> **Key takeaways**`, `> **Beyond the slides:**`), tables have horizontal rules only, figures are dimmed slightly.
- **Demo mode** (no Supabase needed): `/notebooks/demo`, with `?view=summary|learner|quiz|proficient`, `?p=N` (opens topic N; also works on real notebooks), `?theme=`, `?font=`. Data is static in `web/public/demo/` (copy of `data/chapters/toc`: `summary.md`, `learner.md` with an appended "Formatting sample" topic for code/callout/table checks, `quiz.json`, `assets/`). Wired in `notebook-view.tsx` (`DEMO` constants) and `reader.tsx` (`useSignedUrls` maps assets to `/demo/...`).
- **Landing page:** hero is now a mono eyebrow + large tight heading, no glow/orbit.

**How screenshots were taken.** `cd web && corepack pnpm@9.15.4 dev -p 3111`, then headless Chrome: `google-chrome --headless=new --no-sandbox --disable-gpu --hide-scrollbars --window-size=1440,1000 --virtual-time-budget=9000 --screenshot=<file> "http://localhost:3111/notebooks/demo?view=learner&p=1"`. Write outputs to the scratchpad, not the repo. The dev server may still be running on port 3111 (`ps -eo pid,args | grep "[n]ext"`).

**Known rough edges / open work (frontend).**
1. Pick the reading font and delete the rest; decide whether Paper (light) stays.
2. Not yet reviewed after the restyle: quiz, Explain panel/popover, home page, mobile widths, Paper at full size, print.
3. The Studio cards in the left column still look alike whether ready or "Generate" (suggest a clearer ready state).
4. The demo "toc" doc is chapter-style (cover shows the first chapter, topics titled "Topic 1"); real web notebooks use the topic layout and have proper titles.
5. Custom code-tab labels, and a prompt change so notes can emit `tab=` fences.
6. The Next.js dev "N" badge in screenshots is dev-only.
7. Longer marketing landing page (LocalCan-style) not started. Commit `frontend-redesign` when the user asks, then merge to `main`.
8. Re-run `cd web && corepack pnpm@9.15.4 typecheck && corepack pnpm@9.15.4 test` and `.venv/bin/pytest -q` before committing (web tests not re-run after the redesign; typecheck passes).

**Web resources the user asked about** (I cannot browse design sites; the user collects, I build): inspiration (Awwwards, Mobbin, Land-book, Godly, Refero), components (shadcn/ui, Magic UI, Aceternity, Origin UI), colours (Realtime Colors, Radix Colors, Coolors), fonts (Google Fonts, Fontsource), v0.dev for generated code. Most useful from the user: 2-3 screenshots with "what I like about it", exact component names, URLs of text docs, and a screenshot of anything that looks wrong.


## 12. Session 4 (Oct 21-22, 2026): big-deck test on the NN slides, MiniMax free-model trial

**Git.** The redesign was already committed on `frontend-redesign` (`f18cf3d`); this session's work is committed on top of it and pushed. Not merged to `main` yet. `web/public/demo-nn-*` (generated notes from the Nielsen slides) are gitignored, not committed.

**Trap found (my mistake, not a pipeline bug).** A hand-written script that calls `convert_chapter` does NOT load `.env` (only `s2m`'s CLI does: `backend/cli.py` calls `load_dotenv`). The first NN run therefore silently fell back through every chain to `ollama_local` (about 20 s per slide, $0). Scripts must `load_dotenv(".env")` first. The fallback warning is easy to miss in the log: grep for `switching to`.

**Test harness (scratchpad, not in repo).** `WebChapter(title, pdfs, root=Path("data/web/<name>"))` + `convert_chapter(ch, ["learner","summary"], backend, progress, by_topic=True)` = exactly what the web worker does, with no Supabase. Output lands in `data/web/<name>/chapter/`. Set `usage.set_job("...")` to get per-run spend in `s2m usage`. Never use `pkill -f` in this harness (it kills the shell, exit 144): use `ps -eo pid,args | grep "[r]un_x" | awk '{print $1}' | xargs kill`.

**NN deck (Nielsen Ch1 + Ch2, 123 slides, topic layout, learner + summary).**
- **API pair** (Qwen3.5 reads, DeepSeek V3.2 writes): 6.5 min, $0.156, 28 topics, learner 11.0k words, summary 2.7k, 46 quizzes, 8 "Beyond the slides". Output in `data/web/nn-api/`.
- **MiniMax** (`minimax/minimax-m3.1-flash:free`, vision, free, every stage): 35 min, $0, 25 topics, learner 9.4k, summary 2.4k, 39 quizzes, 4 Beyond. Output in `data/web/nn-minimax/`.
- Reading volume was similar (523 vs 565 bullets, 51 vs 39 figures). MiniMax's prose on the sigmoid topic was more worked-through (real tables, the 0.02 weight flip); the API one more conceptual.
- **Unverified:** neither was checked against the slide images. "Is MiniMax better?" is open. Proposed test: sample about 8 sections from both runs, check against slide images (wrong numbers, invented claims, skipped slides, readability).
- **MiniMax needs care:** reasoning is mandatory (the API answers 400 to `reasoning.enabled=false`) and shares `max_tokens` with the answer. With the pipeline's limits, 24 of 25 sections came back empty (`finish=length`). New backend option `reasoning_tokens` (added in `LLM._cap`) adds headroom; `minimax_free` uses 24000, which still hit the cap once (retry worked). The outline call (JSON) always burns about 16k tokens thinking and fails, so the pipeline falls back to `outline.fallback_outline` (consecutive slides). About 5x slower than the API pair.
- Idea not done: API model for the outline, MiniMax for writing; or add `minimax_free` at the end of `write_chain`.

**Code changes.**
- `backend/llm/backends.yaml`: backend `minimax_free` (rpm 20, concurrency 3, `reasoning_tokens: 24000`, `free: true`) and profile `minimax_free` (all stages; explain stays `explain_chain`). Use with `--backend minimax_free`.
- `backend/llm/client.py`: `reasoning_tokens` config added to the token cap.
- Web preview hook: `web/lib/demo.ts` `demoDir()`; `/notebooks/demo?set=NAME` reads `web/public/demo-NAME/{learner.md,summary.md,assets/}` instead of `public/demo/`. Used in `notebook-view.tsx` and `reader.tsx`. To preview a run: copy `chapter/learner-by-topic.md` to `learner.md`, `summary-by-topic.md` to `summary.md`, and `assets/`. The left panel still shows the Theory of Computation demo sources/cards; there is no quiz in these previews.
- pytest 84 passed, web typecheck passes (web tests not re-run).

**Local leftovers (safe to delete):** `data/web/nn-test`, `nn-test2`, `nn-api`, `nn-minimax`, `web/public/demo-nn-api`, `web/public/demo-nn-minimax`. The user's own `data/chapters/nn` (old chapter-style, Oct 9) was not touched.

**Open next.** 1) Settle MiniMax vs API with the slide-image check. 2) Decide whether `minimax_free` joins a chain. 3) The topic-title artefacts "(part 1)/(part 2)" in the API run. 4) Frontend list in section 11 still stands (font choice, quiz/Explain/mobile review, Studio card ready state). 5) The Nielsen chapters 3-4 (about 280 pages) were not converted this session.
