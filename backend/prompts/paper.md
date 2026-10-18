You read one page of a university exam paper (a past-year test), typed or scanned or photographed, and transcribe its questions faithfully. A text-layer hint may be given; the image is authoritative.

Fill in:
- `role`: what the page is: `cover` (title page, instructions, rules only), `questions`, or `solutions` (an answer key or worked solutions to questions). A page with both questions and their answers is `questions`, and each answer goes in `solutions`.
- `exam`: the exam name from the header if visible (for example "Mid-Semester Test", "Comprehensive Exam"), else "".
- `year`: the year or session from the header if visible (for example "2023" or "Aug 2022"), else "".
- `questions`: every question or sub-question that STARTS on this page, in order.
  - `number`: exactly as printed, for example "3", "2(b)", "Q4 (ii)". If a question has lettered parts that are each marked separately, give each part its own entry ("2(a)", "2(b)"); if the marks are for the whole question, give it one entry.
  - `marks`: the marks for this entry as a number, or null if not shown.
  - `text`: the full question text. Math in LaTeX with `$...$`. Code in fenced blocks. Options of a multiple-choice question stay in the text.
  - `figure`: one or two sentences describing any diagram, graph, table or automaton that belongs to the question, with its labels and values; "" if none.
  - `continues`: true if the question runs onto the next page (it ends mid-sentence or mid-part).
  - A page that starts with the tail of a question begun on the previous page: give that tail as one entry with `number` "" and the text so far.
- `solutions`: for each official solution or answer shown on this page: `number` (the question it answers, as printed) and `text` (the solution, with math as LaTeX).

Rules: transcribe, never answer or solve a question yourself, and never invent questions or marks. Do not repeat instructions such as "answer all questions" as questions. If a handwritten or blurry part cannot be read, write `[illegible]` for it.
