You are organizing the slides from one or more lecture PDFs into the table of contents of a single study book. The slides may come from several PDFs (slide ids `A·n`, `B·n`, … mark the PDF and page). The book has **no chapters**: it is one flat list of topic sections that a student reads one after another, like the pages of a textbook.

Build the topic sections:
- **One topic per section.** Group slides by what they teach, not by which lecture or PDF they came from. Never title a section after a PDF, a lecture number or a date.
- **Merge across PDFs.** When a later PDF recaps, repeats or extends something from an earlier one, put those slides in the same section as the original and say so in `notes`. Never create a second section on the same topic.
- **Follow the teaching order.** Keep the lectures' progression, but place a topic after the topics it depends on. Reorder across PDFs only when it clearly helps.
- **Merge** slides that continue one idea ("… (cont.)", step-by-step builds of the same diagram, a definition followed by its example). Example and exercise slides belong with the concept they exercise.
- **Section size.** Aim for 3–8 slides per section: big enough to read as one coherent page, small enough to stay focused. A single dense slide may stand alone. Do not create tiny sections for trivia, and do not stretch one section over several distinct topics.
- **Titles** are specific and descriptive, like a textbook section ("Paging: Address Translation", "Quicksort: Partitioning"), never "Slide 12", "Part 2" or "Introduction (continued)".
- Put slides with no teaching content (course title, agenda, "Questions?", "Thank you", pure references) in `filler`.
- **Placement.** Every slide id must appear exactly once: in one section or in `filler`.
- `chapter_title`: a clean title for the whole book; `overview`: 2–3 sentences on what a student will learn from it.
