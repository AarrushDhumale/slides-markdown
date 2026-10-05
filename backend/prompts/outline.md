You are restructuring the slides of one college course chapter into the outline of a well-organized study document. The chapter may span several PDFs (slide ids `A·n`, `B·n`, … mark the PDF and page).

Produce sections that a student would want in a textbook chapter:
- **Follow the teaching order.** Keep the lecture's progression unless a reorder clearly helps; prerequisites come first.
- **Merge** slides that continue one idea ("… (cont.)", step-by-step builds of the same diagram, a definition followed by its example).
- **Merge across PDFs.** When a later PDF recaps or repeats content from an earlier one, put those slides in the same section as the original and say so in `notes`. Do not create a second section on the same topic.
- **Section size.** Aim for 3–10 slides per section. A single dense slide may stand alone. Prefer 4–12 sections for a typical 40–80 slide chapter.
- **Titles** are descriptive (e.g. "Quicksort: Partitioning", not "Slide 12" or "Part 2").
- Put slides with no teaching content (course title, agenda, "Questions?", "Thank you", pure references) in `filler`.
- **Placement.** Every slide id must appear exactly once: in one section or in `filler`. Example and exercise slides belong with the concept they exercise.
- `chapter_title`: a clean title for the whole chapter; `overview`: what a student will learn.
