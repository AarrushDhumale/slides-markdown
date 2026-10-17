# Reader and writer models, measured on your own slides

The question: should slides be **read** by Qwen3.5-35B-A3B and **written** by DeepSeek V3.2, instead of the earlier default (Gemini 2.5 Flash reads, DeepSeek V3.2 writes)? The writer is the same in both, so the real difference is the reader; Qwen3.5-35B as the writer was tested too.

**Result.** On nine hard pages from three of your decks, Qwen3.5-35B read about as accurately as Gemini 2.5 Flash (each made one real figure error, in different places) at **about half the cost**. As a writer, DeepSeek V3.2 produced cleaner notes than Qwen3.5-35B, and cost about 30% less but ran about 3× slower. The default is now **Qwen reads, DeepSeek writes** (profile `qwen_deepseek`); the Gemini-first setup is profile `api_pair`.

The measurements also exposed three problems in how the models were being called (a request format that silently emptied Qwen's answers, a host that kept DeepSeek "thinking" at your expense, and OpenRouter routing to the dearest host). All three are fixed and described below; they mattered more than the choice of model.

## How it was measured
- **Readers:** the same nine pages through each model, via OpenRouter, using this repo's real extraction prompt, schema and cleanup, with the hosts pinned as in `backends.yaml`. Each output was compared with the slide image. Cost is the billed amount OpenRouter reports for each call.
  - Paging deck (OS): pages 7, 12, 14, 30: bit-field and table diagrams, a 16-row table with repeated rows, a dense multi-level figure, permission-bit tables.
  - Neural networks, chapter 2: pages 23, 37: vector and matrix equations, numeric matrices.
  - POPL: pages 7, 115, 145: a labelled diagram, a raw assembly listing, a scanned textbook paragraph.
- **Writers:** one reading of the whole Paging deck (Qwen3.5-35B), one topic outline (8 sections, made by DeepSeek), then each writer wrote and fact-checked the same first three sections (7 slides) at learner level.
- **Total spend for all of it:** about $0.10 of the key's $10 limit. One run each; model output isn't deterministic.

## Reader results (nine pages)

| Page | What it tests | Qwen3.5-35B | Gemini 2.5 Flash |
|---|---|---|---|
| Paging 7 | x86 page-table-entry bit-field figure | **Wrong:** bit positions off by one (P=1 … G=9, "bits 11–10 reserved"; real: P=0 … G=8, bits 11–9) | Correct |
| Paging 12 | 16-row table with repeated rows, plus a mapping diagram | **Exact:** all 16 rows, right frame numbers | **Wrong:** dropped 2 rows; cited physical pages 7, 8, 26 that aren't on the slide |
| Paging 14 | dense linear vs multi-level page-table figure | Correct, less detail | Correct, more detail (lists the PFN values 12, 13, 100, 86, 15) |
| Paging 30 | permission-bit tables and mappings | Correct | Correct, slightly more detail |
| Neural nets 23 | vector-form backprop equations | Correct | Correct (same LaTeX apart from style) |
| Neural nets 37 | numeric matrices with a Hadamard product | All digits correct | All digits correct |
| POPL 7 | labelled von Neumann diagram | Correct | Correct |
| POPL 115 | raw assembly listing | Duplicated every line as a bullet *and* a code block, and wrapped one line (now cleaned up by `drop_code_duplicates`) | Clean |
| POPL 145 | scanned textbook paragraph | All text, split into sentence bullets, plus three inline words wrongly tagged as code (now removed) | All text as one bullet |

Neither reader is clearly more accurate. Both are strong on equations and ordinary tables. Each made one substantive error on a figure or table with many numbers, and neither is safe to trust on exact numeric details in diagrams.

| Reader (9 hard pages) | Cost per slide | **Per 100 slides** | Tokens in / out per slide | Hidden reasoning tokens |
|---|---|---|---|---|
| Qwen3.5-35B (DeepInfra) | $0.00083 | **$0.083** | 2,600 / 465 | 0 |
| Gemini 2.5 Flash (Google AI Studio) | $0.00158 | **$0.158** | 1,808 / 414 | 0 |

Reading the whole Paging deck with Qwen cost $0.075 per 100 slides, matching the nine-page figure. Qwen sends more tokens per slide (its image encoding is larger) but costs less per token, so it comes out at 0.53× Gemini's cost.

## Writer results (3 sections, 7 slides, draft plus fact-check)

| | DeepSeek V3.2 (text-only) | Qwen3.5-35B (sees the figures) |
|---|---|---|
| Time | 166 s | 51 s |
| Tokens in / out | 14.4k / 5.7k | 20.1k / 5.5k |
| Cost | $0.0059 | $0.0083 |
| Words written vs length budget | 1,020 vs 650 (all 3 sections over 1.35×) | 974 vs 650 (2 of 3 over) |

Reading the notes against the slides:
- **DeepSeek:** clear, well structured, figures embedded properly with short captions, one real "Beyond the slides" note, and the worked examples checked out (frame 5 covers addresses 80–95; the 6-bit translation `010101` → `1110101`). One fault: it said U/S is "user (0) / kernel (1)", which is backwards for x86 and isn't stated on the slide, and it did not flag it as outside the slides.
- **Qwen:** shorter in section 1 and quicker, but it repeated the reader's wrong bit numbers into the notes and into a quiz answer, pasted a long figure description into the prose, left raw `assets/…png` paths in the text instead of embedding the figure, and added a "Beyond the slides" box that was only meta-commentary.
- **Both** overshoot the length budget (the notes run about 140 words per slide against the roughly 100 per slide in your accepted docs), and the fact-check pass caught neither factual slip.

The fact-check compares the notes with the *reader's extraction*, not with the slide image, so a reading error (Qwen's bit positions, Gemini's frame numbers) passes straight through. This is the biggest accuracy risk in the pipeline whichever reader is used.

## Cost per 100 slides (learner + summary)

Reader and writer figures are measured; the summary step is an estimate (about +$0.015, not measured).

| Setup | Reader | Outline + write + review + summary | **Total** | 400 slides / month |
|---|---|---|---|---|
| **B. Qwen reads, DeepSeek writes (default)** | $0.08 | ~$0.11 | **~$0.19** | ~$0.76 |
| A. Gemini reads, DeepSeek writes (`api_pair`) | $0.16 | ~$0.11 | **~$0.27** | ~$1.08 |
| C. Qwen for both | $0.08 | ~$0.15 | **~$0.23** | ~$0.92 |

The nine reader pages were chosen to be hard, so typical slides are cheaper. At your volume the difference between setups is under a dollar a month.

## Three problems found, all fixed

1. **`response_format: json_schema` emptied Qwen's answers.** On OpenRouter's default host, a dense slide came back as the shortest valid JSON (empty arrays, 35 tokens). Asking for plain `json_object`, or nothing, gave the full reading (about 700 tokens). The Qwen backend now uses `json_object`, and `extract_slide` refuses a valid-but-empty reading of a slide that has text (it falls back to the text layer and is not cached).
2. **OpenRouter's headline price is the cheapest host's, and default routing isn't price-first.** Qwen was sent to the dearest host ($0.24 / $1.80 per M instead of the listed $0.15 / $1.00), and some hosts serve fp4, which risks OCR quality. `backends.yaml` now pins fp8 hosts (DeepInfra, Parasail for Qwen).
3. **The "cheapest" DeepSeek host kept thinking.** GMICloud ($0.209 / $0.31) ignored `reasoning: {enabled: false}`: a 120-word answer used 1,460 hidden reasoning tokens (86% of the bill) and took 15–22 s, against 0 hidden tokens and about 6 s on AtlasCloud ($0.26 / $0.38) and Baidu. Two writing calls on GMICloud cost $0.0053 (14.8k output tokens); the whole corrected three-section run cost $0.0059. DeepSeek is now pinned to AtlasCloud and Baidu.

A separate bug was found in my own bake-off harness: it reused job ids across runs, so each run's per-slide cost included earlier runs. It's fixed and covered by a regression test.

## Limits of this evidence
- Nine reader pages and three writer sections from one deck, one run each. This is enough to expose the failure modes above, not to rank the models precisely.
- I judged the outputs by reading them against the slide images; I didn't check every field of every page.
- Gemini 2.5 Flash was called through OpenRouter (Google AI Studio host), not Google's own API; it should be the same model.
- Prices and host availability change; re-check with `GET https://openrouter.ai/api/v1/models/<id>/endpoints`.
- Not measured: the summary step's cost, and how either reader copes with a full 250-slide book.

## Suggested next steps
- Stop the writer repeating exact numeric or positional detail from a figure description unless it also appears in the slide text. DeepSeek happened to avoid copying Qwen's wrong bit numbers; Qwen did not.
- For slides with many numbers, optionally read twice with both models and flag disagreements.
- Look at why both writers overshoot the length budget even after the tighten pass.

## Rerun it

Keys go in `.env` (`OPENROUTER_API_KEY`; `GEMINI_API_KEY` only for the direct-Gemini backends). Use a scratch data folder if you don't want the readings added to `data/cache`:

```bash
cd /home/aarrush/personal/projects/slides_to_markdown
export S2M_DATA=/path/to/scratch          # optional: keeps data/cache untouched (copy a deck's cache dir in first)

# Readers: same pages through both models (prints seconds, tokens and ~$ per 100 slides; writes side-by-side files)
.venv/bin/python eval/bakeoff.py "data/inbox/11 - Paging.pdf" --pages 7,12,14,30 \
    --backends openrouter_qwen35,gemini_25_flash_or

# Writers: one reading, one outline, each writer writes and fact-checks the same sections
.venv/bin/python eval/bakeoff.py "data/inbox/11 - Paging.pdf" --stage write \
    --reader openrouter_qwen35 --writers deepseek_v32,openrouter_qwen35 --sections 3

# The whole default pipeline on a real deck, then check the bill against OpenRouter's dashboard
.venv/bin/s2m convert "data/inbox/11 - Paging.pdf" --chapter paging-test --level learner,summary --by-topic
.venv/bin/s2m usage
```

## Sources
- [Qwen3.5-35B-A3B model card](https://huggingface.co/Qwen/Qwen3.5-35B-A3B): published benchmarks (OCRBench 91.0, OmniDocBench 89.3, CC-OCR 80.7), context length, sampling recommendations. It has no Gemini column, which is why the comparison above was measured.
- [OpenRouter endpoint listing for Qwen3.5-35B-A3B](https://openrouter.ai/api/v1/models/qwen/qwen3.5-35b-a3b/endpoints): the per-host prices and quantizations used here (same URL shape for any model id)
- [Google Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Artificial Analysis: Qwen3.5 35B A3B vs Gemini 2.5 Flash](https://artificialanalysis.ai/models/comparisons/qwen3-5-35b-a3b-non-reasoning-vs-gemini-2-5-flash-preview-09-2025-reasoning): general index only (15 vs 15), not a vision measure
