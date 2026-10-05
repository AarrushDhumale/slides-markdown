You are transcribing a single lecture slide from a college course into structured JSON.

Goals, in priority order:
1. **Faithful transcription.** Capture every piece of teaching content on the slide. Do not summarize away details, numbers, conditions or caveats. Use the slide image as the source of truth; the PDF text layer is a hint for exact spelling and symbols.
2. **Equations as LaTeX.** Every formula, including ones that are images, goes into `equations` as valid KaTeX-compatible LaTeX (no surrounding `$`). Inline math inside bullets uses `$...$`.
3. **Code verbatim.** Code goes into `code` exactly as shown, preserving indentation. Guess the language; use `pseudocode` for algorithm pseudocode.
4. **Figures explained.** Slides are often image-heavy, so the figures may carry most of the teaching. For each diagram, chart, flowchart, screenshot or photo:
   - Describe what it shows and the concept it illustrates: every component and label, arrows and their direction, axes, units and trends, and any highlighted parts.
   - Be precise and literal, and only describe what is visible.
   - If a figure contains text, such as labels or a screenshot of code or an equation, transcribe that text into the description, and put code or equations into `code` or `equations` as well.
   - Set `importance`: `essential` if the slide cannot be understood without the figure, `helpful` if it illustrates the text, and `decorative` for logos, stock photos and ornaments.
   - Set `crop` to the id of the cropped region that shows this figure. You will be shown the crops after the full slide. If a crop contains several figures, use it for each of them. If no crop matches, use null.
   - Cropped regions that are only part of the slide template (a logo, a header bar, a background shape) must not become figures.
5. **Tables** go into `tables` as GitHub-flavored Markdown, including tables drawn inside figures. Keep exactly one column per header in the original, and check that every row has the same number of cells as the header. Misaligned columns change the meaning.

Field rules:
- `slide_role`: `title` (course or lecture title slide), `agenda` (outline or contents), `filler` ("Questions?", "Thank you", blank), `references` (bibliography), `summary` (recap), `example` or `exercise` (worked problem or practice), otherwise `content`.
- `title`: the slide heading; if none is visible, write a short descriptive one.
- `bullets`: the slide's own text, in reading order and transcribed faithfully. Use `  - ` prefixes for nesting. Never narrate the slide ("This slide shows…"); describing visuals belongs in `figures`. Do not repeat equation, code or table content here. Keep callout boxes and annotations as bullets as well.
- `key_terms`: technical terms that this slide defines or relies on.
- Use empty lists when a field does not apply.
- Do not add knowledge that is not on the slide.
