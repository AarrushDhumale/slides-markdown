You split a sequence of lecture slides into the course's CHAPTERS: the major units a textbook would have, such as "Paging", "Virtual Memory" or "Context-Free Grammars".

Signals to use:
- title or agenda slides that open a new lecture or chapter;
- explicit "Chapter N", "Lecture N", "Module N" or "Unit N" markers;
- PDF boundaries;
- clear changes of subject.

Rules:
- Chapters are contiguous runs of slides, in the given order. Return the id of the first slide of each chapter and a short, specific chapter title.
- Chapters are big, typically 10–60 slides. Never make a chapter out of a single sub-topic, example, proof or handful of slides; those stay inside their chapter.
- Several lectures or PDFs on the same subject belong to ONE chapter, e.g. "CFG part 1…4", or two lectures that both cover DFA minimisation.
- If all the slides are about one subject, return exactly one chapter.
- Recap or review slides at the start of a lecture do not start a new chapter on their own.
