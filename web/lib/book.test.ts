import assert from "node:assert/strict";
import { test } from "node:test";

import { parseBook, splitTitle, trimRules } from "./book.ts";

// What backend/pipeline/assemble.py writes for the topic layout.
const DOC = `# Memory Management

> **Level:** Learner · **Sources:** **A** = 11 - Paging, **B** = 12 - Demand Paging

How an OS maps virtual to physical memory.

> **Key takeaways**
>
> - Paging removes external fragmentation

## Contents

- [Paging basics](#paging-basics)
- [Page faults](#page-faults)

---

<!-- slides A·2-4, B·2 -->
## Paging basics

Pages map to frames. ![d](assets/A-002-f1.png)

#### Check your understanding
1. Q?
<details><summary>Answer</summary>

A.

</details>

---

<!-- slides B·3 -->
## Page faults

A fault happens when a page is not resident.
`;

test("parseBook builds a cover and one page per topic", () => {
  const b = parseBook(DOC);
  assert.equal(b.title, "Memory Management");
  assert.deepEqual(b.decks, ["11 - Paging", "12 - Demand Paging"]);
  assert.equal(b.pages.length, 2);
  assert.deepEqual(b.pages.map((p) => [p.number, p.index, p.title]), [[1, 0, "Paging basics"], [2, 1, "Page faults"]]);
  assert.deepEqual(b.pages[0].slides, ["A·2", "A·3", "A·4", "B·2"]);
  assert.equal(b.slideCount, 5);
});

test("page bodies drop the title heading and the separating rules", () => {
  const [p1, p2] = parseBook(DOC).pages;
  assert.ok(p1.body.startsWith("Pages map to frames."));
  assert.ok(!p1.body.includes("## Paging basics") && !/---\s*$/.test(p1.body));
  assert.ok(p1.body.includes("Check your understanding"));
  assert.equal(p2.body, "A fault happens when a page is not resident.");
});

test("the cover intro keeps the overview and takeaways but not the contents list or level line", () => {
  const { intro } = parseBook(DOC);
  assert.ok(intro.startsWith("How an OS maps virtual to physical memory."));
  assert.ok(intro.includes("Key takeaways") && !intro.includes("Contents") && !intro.includes("Level:"));
  assert.ok(!intro.includes("# Memory Management"));
});

test("a doc with no slide anchors has no pages (the reader falls back to plain scrolling)", () => {
  const b = parseBook("# Plain\n\nJust text.\n");
  assert.equal(b.pages.length, 0);
  assert.equal(b.title, "Plain");
});

test("helpers", () => {
  assert.equal(trimRules("text\n\n---\n"), "text");
  assert.deepEqual(splitTitle("## **Bold** title\n\nBody", "x"), { title: "Bold title", body: "Body" });
  assert.deepEqual(splitTitle("No heading here", "Topic 3"), { title: "Topic 3", body: "No heading here" });
});
