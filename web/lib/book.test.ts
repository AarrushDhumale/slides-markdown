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

import { clampPage, pageNumber, parseLibraryBook } from "./book.ts";

const TWO = `# Paging

<!-- slides A·1 -->
## Basics

Pages map to frames.

---

<!-- slides A·2 -->
## Faults

Faults happen.
`;

test("parseLibraryBook: chapter covers, topics in order, placeholders for chapters without a doc", () => {
  const lib = parseLibraryBook([
    { id: "c1", slug: "paging-aaaa1111", title: "paging", status: "ready", md: TWO },
    { id: "c2", slug: "tlb-bbbb2222", title: "tlb", status: "generating", md: null },
  ]);
  assert.deepEqual(lib.entries.map((e) => e.kind), ["chapter", "topic", "topic", "chapter"]);
  assert.equal(lib.chapters[0].title, "Paging");            // heading of the doc, not the file name
  assert.equal(lib.chapters[1].title, "tlb");
  assert.equal(lib.chapters[1].book, null);
  assert.equal(lib.topics, 2);
  assert.equal(pageNumber(lib, 0), 1);
  assert.equal(pageNumber(lib, 0, 1), 3);                   // second topic of chapter 0
  assert.equal(pageNumber(lib, 1), 4);
  assert.equal(clampPage(99, lib), 4);
  assert.equal(clampPage(-3, lib), 0);
});

test("parseLibraryBook: a section choice replaces only that topic", () => {
  const lib = parseLibraryBook(
    [{ id: "c1", slug: "p", title: "p", status: "ready", md: TWO }],
    { "c1:1": { level: "proficient", body: "## Faults, dense\n\nREWRITTEN\n\n---" } },
  );
  const [first, second] = lib.chapters[0].pages;
  assert.equal(first.title, "Basics");
  assert.match(first.body, /Pages map/);
  assert.equal(first.level, undefined);
  assert.equal(second.title, "Faults, dense");
  assert.equal(second.body, "REWRITTEN");
  assert.equal(second.level, "proficient");
  assert.equal(second.index, 1);                            // section index (explain, notes) is unchanged
});
