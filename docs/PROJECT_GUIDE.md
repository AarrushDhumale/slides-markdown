# Slidenotes (slides → markdown): project guide

How a lecture-slide PDF becomes study notes you can read, quiz yourself on, and ask questions about: the whole workflow, every framework and service involved, and why each was chosen.

`README.md` is the quick usage reference and `CLAUDE.md` is the working notes for coding agents. This document is the explanation that connects them.

**Contents**
1. [What the project does](#1-what-the-project-does)
2. [System at a glance](#2-system-at-a-glance)
3. [The conversion pipeline](#3-the-conversion-pipeline)
4. [The model layer](#4-the-model-layer)
5. [The web app](#5-the-web-app)
6. [Frameworks and services, and why each was chosen](#6-frameworks-and-services-and-why-each-was-chosen)
7. [Key design decisions](#7-key-design-decisions)
8. [Cost model](#8-cost-model)
9. [Testing](#9-testing)
10. [Running, developing and deploying](#10-running-developing-and-deploying)
11. [Traps already hit](#11-traps-already-hit)
12. [Status, limits and roadmap](#12-status-limits-and-roadmap)

---

## 1. What the project does

Lecture decks are written to be presented, not read. This project reads a deck the way a person would (text, tables, diagrams, code, equations) and writes it up as Markdown notes at several levels:

| Level | What it is |
|---|---|
| **summary** | The shortest version: key takeaways plus a few dense bullets per section. Condensed *from* the learner notes. |
| **learner** | The main document: full explanations for a college student new to the topic, with an end-of-chapter quiz. |
| **proficient** | A dense revision sheet that assumes familiarity. |
| **donkey** | "Explain like I'm five": analogies first, formal material tucked into collapsible blocks. |

It exists in two forms that share one engine:

- **A CLI** (`s2m`): the original tool. Notes land in `data/chapters/<name>/`.
- **A web app** (`web/`): a NotebookLM-style site. Upload PDFs, read the summary first, switch levels with buttons, take the quiz, and highlight any text or figure to have it explained.

Design rules that came from the user and shape everything below:
- Notes follow the slides. Anything not on a slide goes in a `> **Beyond the slides:**` box, and raw dumps (hex, DWARF) are never decoded beyond what the slide annotates.
- Documents are **chapter-wise**: chapters are detected automatically and must not be fragmented into many small sections.
- Learner notes stay short (a per-section word budget, a 900-word cap, and a tightening pass).

---

## 2. System at a glance

```
                 ┌────────────────────────── web app ───────────────────────────┐
 Browser ──────► │ Vercel: Next.js UI + short API routes                         │
   ▲   │         └──────────────┬────────────────────────────────────────────────┘
   │   │ signed-URL upload      │ rows, files, realtime
   │   ▼                        ▼
   │   Supabase ── Postgres (notebooks, jobs, documents, explain requests)
   │               Storage (PDFs, generated notes, slide images)
   │               Realtime (live progress and streamed explanations)
   │                            ▲
   │                            │ pulls jobs, pushes results (outbound HTTPS only)
   │                     s2m worker (Python)  ◄── laptop today, Cloud Run later
   │                            │
   │                            ▼
   │              convert_chapter(): the pipeline (section 3)
   │                            │
   │                            ▼
   │      model layer (section 4): hosted APIs ─► local Ollama fallback
   │
   └── "Explain this": answered by the worker on the local model, or by the API route on Gemini
```

Three properties define the architecture:

1. **Vercel never runs the pipeline.** Serverless functions time out long before a 5–15 minute conversion, and the models don't live there anyway. Vercel serves the UI and does only short checks (quotas, signed URLs, queueing).
2. **The worker only makes outbound calls.** It pulls jobs from Supabase and pushes results back. No inbound ports, no tunnels to expose, and it can run anywhere with Python: the laptop now, a container later.
3. **The pipeline is one function.** `backend/pipeline/run.py:convert_chapter` is called identically by the CLI and the worker. The web app adds a queue and a UI around the engine; it doesn't fork it.

### Repository map

```
backend/
  cli.py                  s2m convert | inbox | usage | worker | server
  worker.py               web worker: job loop, explain loop, heartbeat, uploads
  paths.py                data roots (override with S2M_DATA)
  llm/
    client.py             LLM + ChainLLM: one client for every backend
    usage.py              token/cost ledger, spend caps, rate limiter
    backends.yaml         every backend, profile and per-stage chain
    remote.py             on-demand vLLM + ssh tunnels (built, unused)
  pipeline/
    ingest.py  chapter.py  extract.py  chapterize.py  outline.py
    per_deck.py  per_slide.py  write.py  assemble.py  summarize.py
    run.py                orchestration
    quiz.py               quiz.json from the learner doc (no model call)
    explain.py            context for "Explain this"
  prompts/                every prompt is a Markdown file (common, learner, summary, explain, ...)
web/                      Next.js app (app/, components/, lib/)
supabase/                 config.toml + migrations/001_init.sql
tests/                    pytest suite (offline)
eval/bakeoff.py           compare models on your own slides
data/                     inbox/, chapters/, cache/ (gitignored)
```

---

## 3. The conversion pipeline

`convert_chapter(chapter, levels, backend, progress, ...)` runs seven stages. Each stage caches its output, so a rerun redoes only what changed.

### 3.1 Ingest: `ingest.py`
PyMuPDF turns each PDF into page PNGs, the text layer and **figure crops**. Cached per PDF hash in `data/cache/<sha>/`; bump `INGEST_VERSION` if this logic changes.

- **Candidates** are raster images plus *vector clusters*: diagrams drawn with shapes in PowerPoint are not images, so drawn shapes are grouped into regions.
- **Template removal.** Logos, banners and rules that repeat on at least 40% of pages are removed *before* merging. Template images are deleted from a scratch copy of the page *by xref*, because blanking pixels damaged figures sitting underneath.
- **Redaction.** Body text crossing a figure, or lying in a picture's margin, is redacted, then whitespace is trimmed, so a crop is just the figure.
- **Identity.** Crops carry a perceptual hash (dHash) plus embedded-image digests, so the same figure on several slides is recognised.

### 3.2 Slides and duplicates: `chapter.py`
Slides get ids like `A·12` (first PDF, page 12). This stage also finds exact duplicate slides, conservative build-up slides (one slide plus a few extra lines) and repeated figures.

### 3.3 Extract: `extract.py`
A vision model sees the full slide, its crops and the PDF text layer as a hint, and returns a `SlideExtract` JSON object: bullets, LaTeX, code, tables, key terms, and figures (description, importance, crop id). Results are cached per (pdf, page, model, `EXTRACT_VERSION`).

- `sanitize()` repairs model quirks: `$`-wrapped LaTeX, narrated bullets ("this slide shows…"), bogus crop ids.
- A slide that fails falls back to its text layer and is not cached, so the next run retries it. The run aborts if more than 30% of slides fail, which usually means the backend is down.

### 3.4 Layout: `chapterize.py`, `outline.py`, `per_deck.py`, `per_slide.py`
The web app uses the **topic layout**: all PDFs are merged into one flat document with no chapters. One model call
(`prompts/topics.md`) groups every slide by topic, merging recaps across PDFs, at about one section per 5 slides with no
12-section ceiling. If that call fails, `fallback_outline` groups consecutive slides instead, so a run is never lost.
`--by-topic` on the CLI gives the same layout.

The CLI default is still **chapter-wise**:
1. One model call detects the course's chapters from title slides, "Lecture N" markers and subject changes.
2. `normalise()` cleans the result in code: chapters are contiguous and ordered, starts snap back to title/agenda slides, same-titled neighbours merge, tiny chapters merge with combined titles. Decks of 14 or fewer teaching slides are one chapter with no model call.
3. Each chapter is split into parts of about 10 slides.
4. **Overlap with earlier chapters** is resolved before writing: bullet-level matches plus one semantic `topic_overlaps` call per group. Sections that are "the same, nothing new" become verbatim **recap** sections with no model rewrite, because writers kept ignoring "don't re-explain" instructions.

Alternative layouts: `--by-topic`, `--per-deck`, `--per-slide`.

### 3.5 Write and fact-check: `write.py`
Each section is written from a level prompt (`common.md` + `<level>.md` + mode extras), then fact-checked against its slides (`review.md`). The fact-check can fix contradictions and move unsupported claims into a `Beyond the slides` box, and its output is rejected if it is gutted, degenerate or breaks rendering. Guards, in order:
1. A **degeneration check** (run-on paragraphs, repeated phrases, the model "thinking out loud") → regenerate from scratch → strip bad paragraphs.
2. A **coverage check**: if the writer skipped slides, retry once listing them.
3. A **length check**: more than 1.35× the word budget triggers one `tighten` pass.

### 3.6 Assemble: `assemble.py`
Normalises Markdown (math, fences, `<details>`), guarantees headings, inserts essential figures the writer left out, turns repeated images into links, drops invented image paths, builds the contents list, and writes `<!-- slides A·4-6 -->` anchors before each section. Those anchors later let the web app link a section back to its slides.

### 3.7 Summary: `summarize.py`
Condenses each learner section (`summary.md`) and adds chapter takeaways. Because it works from the *learner* document, asking for a summary also produces the learner notes.

### Cache keys
Generated prose is cached under a hash of the full prompt, the input and `llm.generation_id` (model plus sampling options). Editing a prompt, changing a model, or changing sampling invalidates exactly the affected cache entries and nothing else. A crashed run, or a re-queued web job, repays only for unfinished calls.

---

## 4. The model layer

All models are called through one client, `backend/llm/client.py`, configured in `backend/llm/backends.yaml`.

### 4.1 Backends
| Kind | Used for |
|---|---|
| `openai` | Any OpenAI-compatible HTTP API: Gemini, OpenRouter (which fronts DeepSeek, Qwen, ...), vLLM |
| `ollama` | Native Ollama API: the laptop's local model and DashBW's shared Ollama |
| `vllm_remote` | An on-demand vLLM server started over ssh (built, never used in production) |
| `chain` | An ordered list of the above, tried in turn |

### 4.2 Two models, one per job
(How the reader and writer candidates compare, with measured token counts and the commands to test them on your slides: [MODEL_COMPARISON.md](MODEL_COMPARISON.md).)

Reading slides and writing notes are different problems, so they use different models (`stages:` in `backends.yaml`):

| Stage | Needs | Default chain |
|---|---|---|
| **extract** | Strong *vision*: dense tables, diagrams, accurate JSON | Qwen3.5-35B-A3B (OpenRouter) → Gemini 2.5 Flash → local Ollama |
| **outline / write / summary** | *Reasoning*, long coherent Markdown and LaTeX, rule-following. Text-only is fine | DeepSeek V3.2 (OpenRouter) → Qwen3.5-35B-A3B → local Ollama |
| **explain** | Fast, short answers; can read images | Local Ollama → Gemini 3.1 Flash-Lite |

Because a text-only writer never sees the pictures, the quality of the extractor's *figure descriptions* becomes critical; that is the main trade-off of the split.

### 4.3 What makes hosted backends robust
- **Retries with backoff and jitter** on 429, 5xx and network errors, honouring `Retry-After`. The SDK's own retries are turned off because they are too short for free-tier limits. Other 4xx errors (bad key, unknown model) fail immediately.
- **Sticky failover in chains.** When a member gives up or has no key, the chain moves to the next one for the rest of the run. `model_id` and `generation_id` follow the active member, so cache keys always name the model that actually wrote the text.
- **Sampling parity.** `options:` are sent to hosted APIs too (`presence_penalty`, `top_p`; `repeat_penalty` becomes `repetition_penalty`, `top_k` via `extra_body`). The Qwen3.5 default `presence_penalty 1.5` made long sections collapse into word salad, so it is overridden to 0 everywhere.
- **Thinking off per provider**, so hidden reasoning tokens don't inflate output cost.
- **Rate limiter** per backend (`rpm:`), so the free tier doesn't hit 429 constantly.
- **`max_output`** caps a runaway generation (the 9B local model once looped for 11.5 minutes on one slide).
- **Vision-less backends** get image parts stripped automatically.

### 4.4 Cost control (`backend/llm/usage.py`)
- Every call is appended to `data/usage.jsonl` with job, stage, backend, tokens and dollars (from the `price:` entry, or OpenRouter's reported cost when present). `s2m usage` summarises the month.
- `S2M_MAX_USD_PER_JOB` (default $1) and `S2M_MAX_USD_PER_MONTH` (default $5) raise `BudgetExceeded` before any paid call that would exceed them. Free and local backends are never capped.
- The pipeline's broad `except Exception` handlers (written so one bad slide can't sink a deck) explicitly re-raise `BudgetExceeded`: a spend cap must stop the run, not be absorbed as "a slide failed".

### 4.5 JSON from models that don't enforce schemas
Ollama doesn't enforce JSON schemas for Qwen3.5, so ids come back as objects, fields go missing and strings go unterminated. The mitigations: the schema is spelled out in the prompt; list fields default to `[]`; id-coercion validators; and `parse_json_reply` tries a strict parse, then lenient parsing, then `close_unterminated_strings`, then `json-repair`.

---

## 5. The web app

### 5.1 What a user sees
- **Home.** A large drop zone ("Drop your lecture PDFs here") and a grid of the user's notebooks with status chips. Dropping files creates a notebook, uploads the PDFs and queues the first job.
- **Notebook page, two panes.**
  - **Left:** the uploaded sources, five **study-tool cards** (Summary, Learner notes, Revision sheet, Explain simply, Quiz) that show ready / generating / "Generate →", and a progress card (stage, count, running cost, or "generator offline, queued").
  - **Right:** the reader. A contents menu, a serif reading column, KaTeX maths, highlighted code, figures with a zoom viewer, "Slides A·4–6" chips that open the source slide images, and `.md` download and print.
- **A book, not a long page.** The notes are one flat list of topics, so the reader presents them like a book: a cover
  (title, overview, key takeaways, numbered contents), then one page per topic with Previous/Next cards, ← → keys, a
  progress bar, read-checkmarks and a remembered place. `web/lib/book.ts` turns the Markdown into cover + pages; a Scroll
  toggle shows everything continuously, and printing always includes every topic.
- **Summary is the default view.** Learner notes come from the same first job; Revision and Explain simply are generated on demand; the quiz needs no extra model call.
- **Quiz.** `backend/pipeline/quiz.py` parses each chapter's "Check your understanding" block (both layouts writers produce) out of the learner doc into `quiz.json`. The UI shows one card at a time with reveal-answer and Got it / Not yet, remembered in `localStorage`.
- **Explain this.** Highlight text, or click a figure or slide: a panel streams a 2–5 sentence explanation, with **Simpler**, **Copy** and up to three follow-up questions, plus a badge showing whether the local model or the cloud answered.

### 5.2 Job lifecycle
1. The browser creates a notebook (`POST /api/notebooks`), asks for a signed upload URL per PDF (`/api/uploads`, which checks size and daily quota), uploads **directly to Storage**, then registers each file (`/api/sources`).
2. `POST /api/jobs` queues a job (`levels`). If an identical job is already queued or running, it is reused rather than paid for twice; daily and global-queue limits apply.
3. The worker calls the `claim_job` function (`FOR UPDATE SKIP LOCKED`), downloads the PDFs to `data/web/notebooks/<id>/` (never `data/chapters/`), counts pages (limit 300 per notebook), and runs `convert_chapter`. The progress callback writes stage and counts to the `jobs` row, throttled to once a second.
4. It uploads `<level>.md`, `assets/`, slide images as WebP, `slides.json`, `extracts.json` and `quiz.json`, then marks the job done. Any failure marks it failed with a readable message and the worker keeps running.
5. The browser sees every change through Supabase Realtime (with a 15-second poll as a fallback).
6. If the worker dies mid-job, `claim_job` reclaims any job whose heartbeat is older than 15 minutes; `Ctrl-C` puts the job straight back in the queue. Finished slides and sections are cached, so only the unfinished calls are redone.

### 5.3 "Explain this" request path
Context is built **server-side**, so a client can only point at things, never inject its own source material. For a selected passage, the model gets: the section's markdown, the extracts (JSON) of exactly that section's slides, and the selection (plus the image, for figures). The section index is "the i-th `<!-- slides … -->` anchor", computed identically in Python (`explain.py`) and TypeScript (`web/lib/slide-anchors.ts`, `explain-context.ts`). The prompt is `backend/prompts/explain.md`, copied into the web app by `pnpm sync-prompt`, with a test that fails if the two copies drift.

`POST /api/explain` then tries, in order:
1. **Cache hit** (same notebook, doc version, selection and history) → instant answer.
2. **Local model.** If a worker heartbeat is fresh and its local model is idle, insert an `explain_requests` row. The worker's explainer loop (its own thread, so explanations never queue behind a conversion) streams `partial_text` into the row, and the browser renders it via Realtime.
3. **Cloud fallback.** If the worker is offline, busy, or hasn't started within 15 seconds, the API route streams Gemini directly (the same prompt) and caches the result.

### 5.4 Security model (phase 1: no visible sign-in)
- Each browser silently gets a Supabase **anonymous** user. Row-level security restricts every table to its owner; notebooks never leak between browsers (verified in a live test).
- The browser can only rename or delete its own notebooks (column-level grant on `title`). Everything else, including creating notebooks, uploads, jobs and explain requests, goes through API routes that use the service-role key after checking quotas.
- `claim_job` and `claim_explain` are executable only by the service role.
- Notes are model output derived from user PDFs, so the Markdown renderer sanitises HTML (`rehype-sanitize`, allowing only `<details>`/`<summary>` beyond the defaults).
- Abuse is bounded by quotas (`web/lib/limits.ts`), a global queue cap, upload size limits, the worker's page cap, and the spend caps.
- Google sign-in can be added later by *linking* to the existing anonymous user, so notebooks carry over and the schema doesn't change.

---

## 6. Frameworks and services, and why each was chosen

### 6.1 Python backend
| Tool | Role | Why |
|---|---|---|
| **Python 3.12** | Everything server-side | The PDF, vision-model and data libraries are Python-native; one language for CLI, pipeline and worker. |
| **PyMuPDF** | PDF → page images, text layer, vector drawings, figure crops | Fast, one library covers rendering *and* the structural data (images, drawing paths, text positions) that figure detection needs. Pure-Python PDF libraries lack rendering and drawing access. |
| **Pillow** | Image hashing, resizing, WebP export | Standard image library; needed for dHash figure identity and for shrinking slide images before upload. |
| **Pydantic v2** | Typed models for slide extracts, outlines, chapter splits | Models return loose JSON; Pydantic validates it, supplies defaults for missing fields, coerces ids, and produces JSON Schema that is spelled out in prompts. |
| **openai SDK** | Client for every OpenAI-compatible API | Gemini, OpenRouter and vLLM all speak this protocol, so one client covers them. Its built-in retries are disabled in favour of our own (section 4.3). |
| **httpx** | Native Ollama calls, Supabase REST/Storage from the worker | Modern sync client with streaming; the worker needs only a handful of REST calls, so a thin client beats a heavy SDK. |
| **PyYAML** | `backends.yaml` | Models, prices, chains and profiles are *data*, so adding a provider is a config edit, not a code change. |
| **json-repair** | Last-resort JSON recovery | Long table transcriptions often produce unterminated strings; repairing beats regenerating a slide that cost a minute. |
| **python-dotenv** | Loads `.env` (API keys, caps) | Keys stay out of the repo; the CLI loads them at start. |
| **pytest** | Test runner | Plain, fixture-based; the suite is fully offline (synthetic PDFs, a fake LLM, an in-memory Supabase). |
| **argparse** | The `s2m` CLI | Standard library, no extra dependency for a handful of subcommands. |

### 6.2 Models and hosts
| Choice | Role | Why |
|---|---|---|
| **Qwen3.5-35B-A3B** | Primary slide reader (OpenRouter); backs DashBW; writer fallback | A mixture-of-experts model (36B total, ~3B active) with native vision. It beat gemma3:12b, ministral-3:8b and minicpm-v on dense tables and diagrams in the bake-off (`eval/bakeoff.py`), and all the prompts were tuned on it, so it is the safe fallback. |
| **Gemini 2.5 Flash** (extract) | Backup reader (`api_pair` profile leads with it) | Strong document and table OCR, schema-enforced JSON. Measured: as accurate as Qwen on nine hard pages but about twice the cost per slide, so it backs Qwen up instead. |
| **DeepSeek V3.2** (write) | Primary writer | Cleaner, better-structured notes than Qwen as a writer in the bake-off, about 30% cheaper, but about 3× slower. Text-only, hence the figure-description trade-off. Pinned to hosts that honour `reasoning: false` (one cheaper host kept thinking, which cost more per answer). |
| **Gemini 3.1 Flash-Lite** | Explain fallback | A fast, cheap, vision-capable model from a different vendor than the writers, so one outage doesn't take everything down. |
| **Ollama + Qwen3.5 9.7B** | Laptop and "Explain this" local model | Already installed; fits the 8 GB RTX 5060 and supports vision. Free and offline, so it is the last step of every chain. The 35B model needs about 23 GB and cannot fit this laptop. |
| **OpenRouter** | One key for DeepSeek and Qwen | A single billing relationship and OpenAI-compatible API for models that otherwise need separate accounts. |
| **DashBW (campus GPUs)** | Original default, now optional | Free shared inference; kept as `--backend dashbw_qwen35`. It was dropped as the default because the web app needs a worker that doesn't depend on campus networking (a Sophos firewall that intercepts HTTPS, and captive-portal logins that expire). |

### 6.3 Web app
| Tool | Role | Why |
|---|---|---|
| **Next.js 15 (App Router)** | Frontend and API routes | Deploys to Vercel with no configuration; API routes keep the service-role key server-side; React Server Components keep the shell light. |
| **React 19 + TypeScript** | UI | Component model suits the reader, panels and live-updating cards; TypeScript catches contract drift with the database. |
| **Tailwind CSS v4** | Styling | Design tokens as CSS variables made light/dark theming a single class switch; no component-library look to fight against. |
| **Radix UI** (dialog, popover) | Accessible primitives | Focus handling, escape-to-close and ARIA for popovers and the figure viewer are easy to get wrong by hand; Radix supplies behaviour and leaves styling to us. |
| **lucide-react** | Icons | Consistent, tree-shakeable. |
| **Geist + Source Serif 4** | Fonts | A clean UI sans for chrome, and a serif designed for long reading for the notes. |
| **react-markdown** with remark/rehype plugins | Rendering the notes | A safe React renderer that turns Markdown into elements (not an HTML string). Plugins: `remark-gfm` (tables), `remark-math` + `rehype-katex` (equations), `rehype-highlight` (code), `rehype-raw` + `rehype-sanitize` (the pipeline's `<details>` quizzes, safely). |
| **KaTeX** | Math | Fast, synchronous rendering; the pipeline already emits `$…$` and `$$…$$`. |
| **sonner** | Toasts | Small, accessible, themeable. |
| **clsx + tailwind-merge** | Class composition | Predictable overrides for variant-based components. |
| **pnpm** (via corepack) | Package manager | This machine has Node but no npm; corepack ships with Node and pins the version. |
| **tsx + node:test** | Web unit tests | The shared doc-splitting helpers are tested without a heavy test framework. |

### 6.4 Backend services
| Service | Role | Why |
|---|---|---|
| **Supabase Postgres** | Notebooks, jobs, documents, explain requests | A real relational store with row-level security. `FOR UPDATE SKIP LOCKED` makes the job table a safe work queue, so no separate queue service is needed. |
| **Supabase Storage** | PDFs, notes, slide images | Signed URLs let the browser upload straight to storage, bypassing Vercel's 4.5 MB request-body limit; private buckets with owner-scoped read policies. |
| **Supabase Realtime** | Live progress and streamed explanations | Row updates push to the browser without us running a websocket server. |
| **Supabase anonymous auth** | Sessions with no visible login | Gives each browser a stable owner id now, and can be upgraded to Google sign-in later without migrating data. |
| **Vercel** | Hosting the Next.js app | The natural home for Next.js; scales to zero, which suits a few uses a month. |
| **Docker + Supabase CLI** | Local development stack | `supabase start` reproduces Postgres, Auth, Storage and Realtime locally from `supabase/config.toml` and the migration. |
| **Playwright (dev only)** | Screenshots and interaction checks | Used to drive real Chrome against the local stack and review the UI at desktop and phone sizes. |

### 6.5 Alternatives considered, and why they lost
- **Run the model on Vercel.** Impossible: no GPU, short function timeouts.
- **Keep DashBW as the production backend.** Free, but tied to campus networking and a shared GPU, and a campus-hosted service is awkward to open to others. Kept as an option, not the default.
- **A GPU you keep running, or serverless GPU.** Usage is a few decks a month and sometimes none, so an always-on or reserved GPU would cost more than all the API calls combined.
- **A separate job-queue service (Redis, SQS).** The Postgres table with `SKIP LOCKED` already does it, in one fewer moving part.
- **Websocket server for streaming.** Realtime on a database row is enough, and survives page reloads.
- **A component library (shadcn, MUI).** The look needed to be specific (NotebookLM-style cards, a reading column), and Radix primitives plus Tailwind got there with less to override.
- **One model for everything.** Simpler, but extraction and writing have different needs; splitting them let each use the cheapest model that is good enough.
- **A model call for the quiz.** The learner notes already contain an answered quiz, so parsing it is free, instant and consistent with the notes.

---

## 7. Key design decisions

| Decision | Rationale |
|---|---|
| One engine, two front ends | `convert_chapter` serves the CLI and the worker, so every pipeline improvement benefits both and nothing is duplicated. |
| Worker *pulls* jobs | No inbound ports, runs behind any firewall, and is trivially replaceable (laptop → container). |
| Caches keyed by prompt + model + sampling | Edits invalidate exactly what they should; a crash or retry never repays finished work. |
| Prompts are Markdown files | Reviewable and diffable, and editable without touching code. |
| Fact-check pass with guards | Models drift from slides; the pass fixes contradictions and quarantines extras, and is itself rejected if it damages the draft. |
| Deterministic recap sections | Telling a model "don't re-explain" didn't hold; copying the earlier text verbatim does. |
| Spend caps that can't be swallowed | A budget guard that a broad `except` could absorb isn't a guard. |
| Anonymous auth first | Lets the UI be perfected and tested with real isolation now, and real sign-in later without a migration. |
| Same prompt in Python and TypeScript, with a drift test | Local and cloud explanations behave identically, and the copies can't silently diverge. |
| Local model first for Explain | Free and fast enough for 150–250 token answers; the cloud only covers the offline or busy case. |

---

## 8. Cost model

Measured with a live OpenRouter key on this project's own slides (full results in [MODEL_COMPARISON.md](MODEL_COMPARISON.md)); the summary step is estimated.

| Setup | Reader | Outline + write + review + summary | **Per 100 slides** | At ~400 slides/month |
|---|---|---|---|---|
| **Default: Qwen3.5-35B reads, DeepSeek V3.2 writes** (`qwen_deepseek`) | $0.08 | ~$0.11 | **~$0.19** | ~$0.76 |
| Gemini 2.5 Flash reads, DeepSeek writes (`api_pair`) | $0.16 | ~$0.11 | ~$0.27 | ~$1.08 |
| Qwen3.5-35B for both | $0.08 | ~$0.15 | ~$0.23 | ~$0.92 |
| Gemini free tier (`dev_free`) | $0, rate-limited, prompts may be used for training | | $0 | $0 |
| Laptop, local Qwen3.5 9.7B (`local`) | $0, roughly 1–2 hours | | $0 | $0 |

The reader figures come from nine deliberately hard pages, so typical slides cost less. Each extra level adds a similar writing cost without re-reading the slides. At this volume the bill is about a dollar a month, so models were chosen on measured quality and reliability, not price. Prices and host availability change: re-check with the OpenRouter endpoints listing, and trust the usage ledger's billed costs over any listing.

---

## 9. Testing

All tests run offline: no GPU, no network, no real Supabase.

- **`.venv/bin/pytest -q`: 77 tests.**
  - `test_pipeline.py`: synthetic PDFs with raster and vector diagrams and template logos, run end to end through a deterministic fake LLM in every layout mode, plus failure injection (a flaky model, a rambling model).
  - `test_llm_chain.py`: retry and `Retry-After`, fail-fast on 4xx, chain failover and stickiness, a missing key, sampling options forwarded, text-only image stripping, ledger and price maths, per-job and monthly caps, budget not failed over, rate limiting, streaming failover, `max_output`.
  - `test_bakeoff.py`: the reader and writer bake-off harness (`eval/bakeoff.py`) end to end with the fake LLM.
  - `test_worker.py`: a job end to end against an in-memory Supabase; failure reporting; interruption requeues; Explain streaming and cache; bad Explain requests; context building and validation; quiz parsing in both layouts; the prompt-sync check.
- **`cd web && pnpm test`: 10 tests** for the doc-splitting, slug and book-parsing helpers that must match Python.
- **Checked live, not in the suite:**
  - the SQL migration on a real Postgres: job claiming, stale-job reclaim, row-level security, and the column and function grants;
  - the whole stack on a local Supabase: uploads, quotas, ownership checks, a 6-slide deck converted on the laptop model, Explain streaming for text and figures, and on-demand generation;
  - screenshots in light and dark and at phone width.

---

## 10. Running, developing and deploying

### CLI
```bash
cd /home/aarrush/personal/projects/slides_to_markdown
.venv/bin/s2m convert data/inbox/toc/*.pdf --chapter "toc" --level learner,summary
.venv/bin/s2m usage                      # API spend this month
.venv/bin/s2m convert deck.pdf --backend dev_free     # Gemini free tier
.venv/bin/s2m convert deck.pdf --backend local        # laptop only
```
Profiles: `qwen_deepseek` (= default), `api_pair`, `dev_free`, `local`, `dashbw_qwen35`, `dashbw_small`.

### Local web development
1. Copy `.env.example` to `.env` and fill in `GEMINI_API_KEY` and `OPENROUTER_API_KEY` (leave blank to use only `local`).
2. `supabase start` (Docker), then put its API URL and keys into `web/.env.local` (see `web/.env.example`) and into `.env` as `SUPABASE_URL` and `SUPABASE_SERVICE_KEY`.
3. `cd web && corepack pnpm@9.15.4 dev`.
4. `.venv/bin/s2m worker`, with Ollama running `qwen3.5:latest` and `OLLAMA_KEEP_ALIVE=30m` if you want the local Explain model.

### Putting it online
1. Create a Supabase project, run `supabase/migrations/001_init.sql` in the SQL editor, and enable **Anonymous sign-ins** under Authentication → Providers.
2. Create a Vercel project from the repo with **Root Directory = `web`**, and set `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY` and `ADMIN_TOKEN`.
3. On the worker machine, set `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` to the hosted project and run `s2m worker` in tmux or as a `systemd --user` service.
4. Set a credit limit on the OpenRouter key and a budget alert on the Google project, in addition to the in-app caps.
5. `/admin?token=…` shows jobs, workers and monthly spend.

---

## 11. Traps already hit

- **Qwen3.5's default `presence_penalty 1.5`** collapses long sections into word salad. Override it everywhere (done in `backends.yaml`, including the local and hosted Qwen entries).
- **Ollama doesn't enforce JSON schemas** for this model; rely on the in-prompt schema and the repair chain (section 4.5).
- **With thinking off, the model "thinks out loud"** in its answers; `degeneration()` catches it.
- **A small local model can loop** on one slide for many minutes; `max_output` bounds it.
- **A `now()` string is not a timestamp** over the Supabase REST API; send real ISO times.
- **Broad `except Exception` swallowed the spend cap** until `BudgetExceeded` was re-raised explicitly.
- **`response_format: json_schema` can empty a model's answer.** Qwen3.5-35B on OpenRouter's default host answered a dense
  slide with the shortest valid JSON (empty arrays, 35 tokens); `json_object` or no format gave the full reading. The
  Qwen backend now uses `json_object`, and `extract_slide` refuses a valid-but-empty reading of a slide that has text
  (it falls back to the text layer and is never cached).
- **OpenRouter's headline price is the cheapest host's, and default routing isn't price-first.** Qwen was routed to the
  dearest host ($0.24/$1.80 vs the listed $0.15/$1.00), and some hosts serve fp4. `backends.yaml` pins fp8 hosts
  (`provider.order`, `quantizations`, `require_parameters`); check live prices with
  `GET /api/v1/models/<id>/endpoints`, and use the usage ledger (OpenRouter reports the billed cost) to confirm.
- **A bake-off harness must not reuse job ids**, or each run's per-slide cost silently includes earlier runs (fixed;
  `test_repeated_runs_do_not_inherit_each_others_token_totals`).
- **Vector diagrams aren't images**, so figure detection must cluster drawn shapes, and template images must be removed by xref, not by blanking pixels.
- **Vercel's 4.5 MB body limit** rules out uploading PDFs through an API route; use signed storage URLs.
- **The campus network** (HTTPS interception, expiring captive-portal login) is why the worker doesn't run on DashBW.
- **Tooling quirks on this machine:** no npm (use `corepack pnpm@9.15.4`), Node has no built-in TypeScript (use `tsx`), the system `timeout` needs an absolute path to `s2m`, and `pkill -f` matches its own shell.

---

## 12. Status, limits and roadmap

**Built and verified:** the pipeline and its guards; the hosted-API model layer with chains, caps and a ledger; the worker; the Supabase schema; the web app (home, notebook, reader, quiz, slide popovers, Explain panel, admin page); and the offline test suite.

**Not yet verified against real services:**
- No paid or free-tier API call has been made yet. The Gemini 3.x model names (such as `gemini-3.1-flash-lite`) are unconfirmed; if one is wrong, that member fails and the chain moves on.
- Real-bill accuracy of the usage ledger, and the Gemini fallback for Explain.
- A hosted (not local) Supabase and a Vercel deployment.

**Known limits**
- Slides that are raw listings (assembly, DWARF) still produce guessy explanations.
- Topic-overlap detection between decks relies on a model call.
- A text-only writer sees figure descriptions, not figures; if bake-offs show diagram-heavy decks suffer, a vision writer can be used for sections with essential figures.
- The local 9B model reads slides at about 50 seconds each and writes a section in about 2 minutes: fine as a fallback, slow as a default.

**Roadmap**
- Run the reader and writer bake-offs (`eval/bakeoff.py --stage extract|write`, see [MODEL_COMPARISON.md](MODEL_COMPARISON.md)) to decide between `api_pair` and `qwen_deepseek`.
- Google sign-in restricted to the campus domain, linked to the anonymous user.
- A container image for the worker and a Cloud Run Job, plus mirroring the caches to Storage so a worker with ephemeral disk still reuses paid work.
- A slide pane that follows scrolling, and a "retry failed slides" action.
- Try `think: true` for writing (cleaner prose, slower), and a per-chapter progress display in the CLI.
