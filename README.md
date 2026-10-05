# slides → markdown

Turns lecture slide PDFs into readable Markdown study docs at three levels:
**proficient** (revision overview), **learner** (full explanations), **dockey** (explain like I'm five).
A fourth output, **summary**, is a concise quick-revision version condensed from the learner doc. It has chapter
key takeaways, then 3–7 dense bullets per section.

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
s2m convert x.pdf --backend gemini                         # any backend in backends.yaml
s2m server status | up | down
```

Output goes to `data/chapters/<chapter>/{proficient,learner,dockey,summary}.md` plus `assets/`.
`--level summary` reuses saved learner sections when they exist. `--no-review` skips the fact-check pass.

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

**Default: models in DashBW's shared Ollama**, reached through an `ssh -L` tunnel. Ollama loads models when they're called and unloads them when idle.
- Slide reading: `ministral-3:8b`.
- Grouping and writing: `qwen3:14b`.
- `--backend dashbw_qwen35` uses Qwen3.5-35B-A3B for everything. It's multimodal, so the writer also sees the figures.

**On-demand vLLM** (`dashbw`, `dashbw_122b`, `dash5000`): `s2m server setup|up|down`. The server starts on the GPU with the most free memory, and a remote watchdog stops it after 15 idle minutes.

**Other backends:**
- `ollama_local`: laptop, offline.
- `openrouter` and `gemini`: cloud, needing `OPENROUTER_API_KEY` or `GEMINI_API_KEY`.

**Comparing models** on your own slides:
```bash
python eval/bakeoff.py "data/inbox/deck.pdf" --pages 3,8,30 --backends dashbw_ministral3,dashbw_qwen35
```
Results go to `eval/results/<deck>/pNNN.md`, with each slide image next to every backend's extraction and timing.

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

## Tests
`.venv/bin/pytest -q`. These run offline: they use synthetic PDFs and a fake LLM.
