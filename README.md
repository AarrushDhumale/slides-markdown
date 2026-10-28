# slides → markdown

For the full workflow, architecture and the reasoning behind every framework, see [docs/PROJECT_GUIDE.md](docs/PROJECT_GUIDE.md).

Turns lecture slide PDFs into readable Markdown study docs at three levels:
**proficient** (revision overview), **learner** (full explanations), **donkey** (explain like I'm five).
Two more outputs are condensed from the learner doc: **notes** (short lecture notes you could write down, keeping
the important diagrams) and **summary** (chapter key takeaways, then 3–7 dense bullets per section).

Several PDFs can form one **chapter** and become one doc. Slides are referenced as `A·12` (first PDF, page 12), `B·3` (second PDF), and so on.

## Setup
```bash
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/s2m server setup            # one-off: installs vLLM on DashBW in ~/s2m-vllm
```

## Use
```bash
s2m convert lec1.pdf lec2.pdf --chapter "Ch3 Sorting"     # asks which level(s)
s2m convert lec1.pdf --level all --open
s2m inbox --level learner      # data/inbox/<chapter folder>/*.pdf, or loose PDFs
s2m convert x.pdf --backend dev_free                       # any backend or profile in backends.yaml
s2m usage                                                  # API tokens and $ this month
s2m server status | up | down
```

Output goes to `data/chapters/<chapter>/{proficient,learner,donkey,notes,summary}.md` plus `assets/`.
`--level notes` and `--level summary` reuse saved learner sections when they exist. `--no-review` skips the fact-check pass.
Generated caches (sections, outlines, overlaps) live in a hidden `.cache/` inside each chapter folder, so the folder
shows only the docs. Older chapters are moved there automatically on the next run, with no regeneration.

**Past-year papers.** Put papers (typed or scanned PDFs, and optionally their solution PDFs) in
`data/inbox/<chapter>/papers/`, or pass `--papers p1.pdf p2.pdf solutions.pdf` to `s2m convert`. A file whose name
contains `solution`, `soln`, `answer` or `key` is paired with the paper it matches best. Each question is mapped to the
topics of the notes, and you get `papers.md` (topics ranked by marks, question types, repeated questions, questions the
slides don't cover, every question with its official solution) and `papers.json`. Every doc also gets an
`Asked in: Midsem 2023 Q4 (8 m)` line under the topics that were asked, and a ★ on the high-yield ones.
Solutions are only shown when the paper came with them; none are written by a model.

**Layout: chapter-wise by default.**
- **Chapter detection.** The course's chapters are detected automatically from title slides, "Lecture/Chapter N" markers, PDF boundaries and subject changes. A combined PDF is split into its chapters, and several PDFs on one subject become one chapter.
- **Clean-up in code.** Chapters are made contiguous and big enough: tiny ones merge into a neighbour, neighbours with the same title merge, and each start moves back to the lecture's title slide.
- **Single topics stay whole.** An upload with 14 or fewer teaching slides is always one chapter, with no detection call.
- **Inside a chapter** the text reads continuously. Long chapters are written in parts of about 10 slides with natural sub-headings and one quiz at the end of the chapter. The contents list shows chapters only.
- **Repeats.** Overlap with earlier chapters becomes a short recap and link.
- **Output** goes to `<level>.md`. Detected chapters are cached in `chapters.json`.

Other layouts: `--by-topic` (many small topic sections, `<level>-by-topic.md`), `--per-deck`, and `--per-slide`.

**Per-deck mode** (`--per-deck`) gives one part per PDF, in the order given, written to `<level>-by-deck.md`. Each part (`## Deck N · <lecture title>`, with its source file) is split into its own topic sections; topics are never merged across decks. Overlap between decks, such as a lecture recapping an earlier one, is handled before writing:
- Repeated bullets, formulas, code, tables and figures get a one-line reminder linking to the earlier deck, and the section's budget shrinks.
- Exact recap slides are noted in the deck's intro.
```bash
s2m convert data/inbox/toc/*.pdf --chapter toc --level learner,summary --per-deck
```

**Per-slide mode** (`--per-slide`) gives one section per slide, in deck order, written to `<level>-by-slide.md`. N slides give N sections, headed `Slide N · <title>`. Overlap is resolved before writing:
- **An exact repeat** of an earlier slide becomes a one-line link to it.
- **Title, agenda and "Questions?" slides** become a one-line note.
- **Bullets, formulas, code, tables and figures that already appeared** on an earlier slide are listed for the writer. It explains only what's new on the slide and gives each repeated item a one-sentence reminder linking back. Repeated figures aren't embedded again, and the word budget shrinks to match.
- **Build-up slides** (one slide plus a few extra lines) are both kept, and the later one explains just the additions.
```bash
s2m convert data/inbox/popl/midsem.pdf --chapter popl --level learner,summary --per-slide
```

## How it works
1. **Ingest** (PyMuPDF): page PNGs, the text layer and **figure crops**, cached per PDF hash in `data/cache/`.
   - Crops cover both pasted-in images and diagrams drawn with shapes (flowcharts, trees, charts made in PowerPoint).
   - Overlapping regions are merged.
   - Logos and template elements repeated across slides are filtered out.
2. **Extract**: a VLM sees the full slide plus every crop, using the text layer as a hint. It returns JSON: bullets, LaTeX, code, tables, and for each figure a description, the matching crop, and an importance level (`essential`, `helpful` or `decorative`). Cached per PDF, page and model.
3. **Outline**: one call groups every slide of the chapter into sections, merging recaps across PDFs.
   - Near-identical repeated slides are dropped before this step.
   - Title and filler slides never become sections.
   - Continuation slides (marked "(cont.)") are merged into the section before them.
4. **Write**: each section is written at the chosen level (`backend/prompts/*.md`), in parallel.
   - The writer is told which figures exist and must embed and explain the essential ones.
   - Backends marked `vision: true` also *see* the figures, not just their descriptions.
5. **Assemble**:
   - Adds a table of contents and `<!-- slides A·4-6, B·2 -->` source anchors.
   - Checks math and code-fence formatting.
   - Copies figure crops to `assets/`, or the whole slide when no crop fits.
   - Re-inserts any essential figure the writer left out, with its description as a caption.
   - Removes any image path the writer invented.

Changing the level reruns only step 4. Reordering a chapter's PDFs reruns only steps 3–4.

## Backends
`backend/llm/backends.yaml` maps each stage to a backend. `--backend NAME` overrides it with a single backend or a **profile**, which is a per-stage mapping.

**Default: hosted APIs, two models, both on OpenRouter (one key).** Slide reading uses a vision model; outlining, writing,
the fact-check and the summary use a text model. Each stage is a fallback **chain**:
- `extract_chain_qwen` (default reader): Qwen3.5-35B-A3B (OpenRouter) → Gemini 2.5 Flash → local Ollama;
- `write_chain`: DeepSeek V3.2 (OpenRouter, text-only: it gets the figure *descriptions*) → Qwen3.5 → local Ollama;
- `explain_chain` (web "Explain this"): local Ollama → Gemini 3.1 Flash-Lite.

Measured on this project's own slides ([docs/MODEL_COMPARISON.md](docs/MODEL_COMPARISON.md)): Qwen read about as accurately
as Gemini 2.5 Flash at roughly half the cost, and DeepSeek wrote cleaner notes than Qwen. OpenRouter hosts differ a lot, so
`backends.yaml` pins fp8 hosts that honour the settings used (see the traps in the guide).

Every backend retries 429s, 5xx and network errors with backoff (honouring `Retry-After`). When one gives up, or has
no key, the chain switches to the next for the rest of the run. Profiles: `qwen_deepseek` (= the default), `api_pair`
(Gemini 2.5 Flash reads; needs `GEMINI_API_KEY`), `dev_free` (Gemini free tier, $0 but rate-limited), `local` (laptop only),
and the DashBW ones below.

**Cost control.** Every call is logged with its tokens and $ to `data/usage.jsonl`; `s2m usage` sums it by backend and job.
`S2M_MAX_USD_PER_JOB` (default $1) and `S2M_MAX_USD_PER_MONTH` (default $5) stop a run before a paid call that would go
over; local and free backends are never capped. Measured: about **$0.19 per 100 slides** for learner + summary with the
default pair (reader $0.08, writing side about $0.11), against about $0.27 with the Gemini-first `api_pair`.

**Campus GPU (DashBW)**, via `--backend dashbw_qwen35` etc.:

Models in DashBW's shared Ollama, reached through an `ssh -L` tunnel. Ollama loads models when they're called and unloads them when idle.
- Slide reading: `ministral-3:8b`.
- Grouping and writing: `qwen3:14b`.
- `--backend dashbw_qwen35` uses Qwen3.5-35B-A3B for everything. It's multimodal, so the writer also sees the figures.

**On-demand vLLM** (`dashbw`, `dashbw_122b`, `dash5000`): `s2m server setup|up|down`. The server starts on the GPU with the most free memory, and a remote watchdog stops it after 15 idle minutes.

**Other backends:**
- `ollama_local`: laptop, offline (`qwen3.5:latest`, 9.7B with vision; fits the 8 GB RTX 5060).
- Hosted APIs (keys in `.env`, see `.env.example`): `gemini_25_flash`, `gemini_25_flash_lite`, `gemini_flash_lite`,
  `gemini_free_flash` / `gemini_free_lite` (free-tier key), `deepseek_v32` and `openrouter_qwen35` (OpenRouter).

**Comparing models** on your own slides (full write-up and numbers: [docs/MODEL_COMPARISON.md](docs/MODEL_COMPARISON.md)):
```bash
# readers: the same pages through several backends; prints seconds, tokens and ~$ per 100 slides
python eval/bakeoff.py "data/inbox/deck.pdf" --pages 3,8,30 --backends gemini_25_flash,openrouter_qwen35
# writers: the same slides and outline, each writer writes (and fact-checks) the same topic sections
python eval/bakeoff.py "data/inbox/deck.pdf" --stage write --reader openrouter_qwen35 \
    --writers deepseek_v32,openrouter_qwen35 --sections 3
```
Results go to `eval/results/<deck>/`: for readers, each slide image next to every backend's extraction (`pNNN.md`); for
writers, one Markdown file per writer plus `summary.md` (tokens, $, words vs budget, skipped slides, broken Markdown).
Profile `qwen_deepseek` is Qwen3.5-35B reading and DeepSeek V3.2 writing, both on OpenRouter (one key).

## Output quality guards
- **Fact-check pass:** after a section is written, the model compares it with its slides. It fixes contradictions and moves unsupported extras into a `> **Beyond the slides:**` box, or deletes them.
- **Broken-output detector** (`assemble.degeneration`) catches:
  - run-on paragraphs;
  - long unpunctuated word runs;
  - repeated phrases;
  - the model "thinking out loud" ("Wait, let's re-check…").

  A flagged draft is regenerated from scratch, then cleaned of the affected paragraphs if needed. The detector also checks the fact-check output and runs once more at assembly.
- **Sampling:** Qwen3.5's Ollama defaults (presence_penalty 1.5) made long sections collapse into word salad, so `backends.yaml` sets sampling options explicitly.
- **Section size:** sections are limited to 8 slides, and the output length follows a per-section word budget.

## Failure handling
- **A slide the model can't read** (bad JSON, timeout) falls back to its PDF text plus figure crops. That result isn't cached, so the next run retries it. The run stops instead if more than 30% of slides fail, which usually means the backend is down.
- **A section that fails twice** is rendered from the raw slide content, with a note. The rest of the document is unaffected.
- **Ollama calls** retry network errors, timeouts and server errors with backoff. A missing model gives an `ollama pull …` hint.

## Debugging figure detection
- `figures/pNNN-fK.png` in `data/cache/<pdf-sha>/` holds each crop exactly as it's embedded.
- `meta.json` holds each crop's bounding box, source (raster or vector) and image digests.

## Web app
A NotebookLM-style site (`web/`, Next.js on Vercel): drop PDFs, read the summary first, then learner notes, revision
sheet, explain-simply version and a quiz; highlight any text or click a figure to have it explained.

**One chapter per PDF.** A notebook is a list of chapters, one for each PDF you add. Each chapter is a **book of topics**:
the model groups its slides by *topic* (about one section per 5 slides, titled like a textbook section). The reader shows a
cover with the chapters, then each chapter's cover and one page per topic, with Previous/Next cards, ← → keys, a progress
bar, checkmarks for topics you've read, and your place remembered. A **Scroll** toggle gives one continuous page, and Print
always prints every topic. **Chapters appear as soon as they are written**: with several PDFs you can read chapter 1 while
chapter 2 is still being generated (the others show "Writing…" or "Waiting"). The CLI keeps its chapter-wise default;
`s2m worker --layout chapters` uses the CLI's chapter detection inside each PDF.

**Shared chapter library.** A chapter is identified by its PDF's content hash (computed server-side), so a PDF anyone has
already converted is never converted, or paid for, again: the new notebook links to the existing chapter and it is ready
at once, even if it was made by another user. Generated files live in the `library` bucket under the chapter's name
(`<title>-<hash>/…`). The *Study tools* cards show "2 of 5 chapters" when only some chapters have a level, and a job only
generates what is missing. `s2m library [--missing]` lists which chapters and levels exist.

**Per-section level.** Each topic has a **Level** menu (Learner / Revision / Explain simply). It rewrites only that topic
through OpenRouter (DeepSeek V3.2, from the web server, so it works while the worker is off) and keeps your choice for the
notebook; "Back to the default" undoes it. Rewrites are cached per chapter and shared; if the chapter already has a full
doc at that level, the section is copied from it at no cost. Limited to 20 new rewrites per hour per user.

**My notes.** The **Notes** button on each topic opens a panel (side column on wide screens, bottom sheet on phones) where
you write your own thoughts, saved automatically to your account per topic. The *My notes* card lists everything you wrote,
grouped by chapter, with jump links and a `.md` download.

```
Browser ─► Vercel (web/: UI + short API routes) ─► Supabase (Postgres, Storage, Realtime)
                                                       ▲ pulls jobs, pushes results
                                       s2m worker (laptop now; Cloud Run later) ─► model APIs / local Ollama
```
- **No sign-in yet:** each browser gets an anonymous Supabase user; row-level security keeps its notebooks private.
  Quotas (`web/lib/limits.ts`) plus the worker's spend caps bound abuse.
- **Worker:** `s2m worker` claims queued jobs (`claim_job`) and, for each PDF of the notebook in order, claims the chapter
  (`claim_chapter`, so two jobs never write the same one), runs `convert_chapter` for the levels it lacks (learner + summary
  first; other levels on demand) and publishes it to the `library` bucket (`<level>.md`, `assets/`, slide images,
  `extracts.json`, `quiz.json` parsed from the learner doc) as soon as it is done. A failed chapter doesn't stop the others.
  It also answers "Explain this" requests with the local model, streaming into the row. Local work lives in
  `data/web/library/<chapter>/` (shared by every notebook) and `data/web/notebooks/<id>/pdfs/`, never `data/chapters/`.
- **Explain this:** local model first (free, a few seconds); if the worker is offline or busy, the API route answers
  with Gemini directly. Same prompt either way (`backend/prompts/explain.md`, synced into `web/` by `pnpm sync-prompt`).

**Setup**
1. Supabase project → SQL editor → run `supabase/migrations/001_init.sql`, `002_notes_donkey.sql` and `003_library.sql` in order; Authentication → enable **Anonymous sign-ins**.
2. Vercel → import the repo, **Root Directory = `web`**, env vars from `web/.env.example` (`OPENROUTER_API_KEY` is for the per-section rewrites).
3. Worker: copy `.env.example` to `.env` (API keys, caps, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`), keep Ollama running
   with `qwen3.5:latest` (`OLLAMA_KEEP_ALIVE=30m`), then `.venv/bin/s2m worker` in tmux.

**Local development:** `supabase start` (Docker) applies the migration and enables anonymous sign-ins from
`supabase/config.toml`; put its URL and keys in `web/.env.local` and `.env`; `cd web && pnpm dev`.

## Tests
`.venv/bin/pytest -q`. These run offline: they use synthetic PDFs, a fake LLM and an in-memory Supabase.
`cd web && pnpm test` checks the doc-splitting, multi-chapter book and rewrite-prompt helpers shared with the worker.
