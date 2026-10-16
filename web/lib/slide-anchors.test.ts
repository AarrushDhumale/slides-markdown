import assert from "node:assert/strict";
import { test } from "node:test";

import { anchor, assetPaths, expandIds, slideFileId, splitDoc, stripContents, toc } from "./slide-anchors.ts";

const DOC = `# T

## Contents
- [Paging](#paging)

<!-- slides A·2-4, B·7 -->
## Paging

Pages map to frames. ![d](assets/A-002-f1.png)

<!-- slides B·8 -->
## TLB

A cache of translations.
`;

test("expandIds mirrors the Python helper", () => {
  assert.deepEqual(expandIds("A·2-4, B·7"), ["A·2", "A·3", "A·4", "B·7"]);
  assert.deepEqual(expandIds("junk"), []);
});

test("splitDoc indexes sections like the worker", () => {
  const { preamble, sections } = splitDoc(DOC);
  assert.ok(preamble.startsWith("# T"));
  assert.deepEqual(sections.map((s) => s.slides), [["A·2", "A·3", "A·4", "B·7"], ["B·8"]]);
  assert.ok(sections[1].md.startsWith("## TLB"));
});

test("toc and anchors", () => {
  assert.deepEqual(toc(DOC).map((t) => t.id), ["paging", "tlb"]);
  assert.equal(anchor("Chapter 1 · Course Intro"), "chapter-1--course-intro");
  assert.equal(slideFileId("B·12"), "B-012");
});

test("stripContents and assetPaths", () => {
  assert.ok(!stripContents(DOC).includes("## Contents"));
  assert.ok(!stripContents(DOC).includes("<!-- slides"));
  assert.deepEqual(assetPaths(DOC), ["assets/A-002-f1.png"]);
});

test("stripContents removes the whole contents list and the level line", () => {
  const md = "# T\n\n> **Level:** Summary · **Sources:** A\n\n> **Key takeaways**\n> - x\n\n## Contents\n\n- [Ch 1](#ch-1)\n- [Ch 2](#ch-2)\n";
  const out = stripContents(md);
  assert.ok(!out.includes("Ch 1") && !out.includes("Level:"));
  assert.ok(out.includes("Key takeaways"));
});
