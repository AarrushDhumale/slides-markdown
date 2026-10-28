import assert from "node:assert/strict";
import { test } from "node:test";

import { chapterSlug, missingLevels } from "./library.ts";
import { replaceSection, splitDoc } from "./slide-anchors.ts";

test("missingLevels: nothing missing when everything exists", () => {
  assert.deepEqual(missingLevels(["learner", "summary", "quiz"], ["summary", "learner"]), []);
});

test("missingLevels: summary pulls in a missing learner doc, but not an existing one", () => {
  assert.deepEqual(missingLevels([], ["summary"]), ["summary", "learner"]);
  assert.deepEqual(missingLevels(["learner"], ["summary"]), ["summary"]);
  assert.deepEqual(missingLevels(["summary"], ["learner"]), ["learner"]);
});

test("chapterSlug is ascii, readable and ends with the hash prefix", () => {
  assert.equal(chapterSlug("12 - Demand Paging.pdf", "3f9a1c2b44aa0011"), "12-demand-paging-3f9a1c2b");
  assert.equal(chapterSlug("日本語.pdf", "3f9a1c2b44aa0011"), "3f9a1c2b");
  assert.match(chapterSlug("A very long name ".repeat(10) + ".pdf", "00112233aabbccdd"), /^[a-z0-9][a-z0-9-]*-00112233$/);
});

const DOC = `# T

<!-- slides A·2-4 -->
## Paging

Pages map to frames.

---

<!-- slides B·8 -->
## TLB

A cache.
`;

test("replaceSection keeps anchors and the other sections", () => {
  const out = replaceSection(DOC, 0, "## Paging\n\nRewritten.\n\n---");
  const { sections } = splitDoc(out);
  assert.equal(sections.length, 2);
  assert.deepEqual(sections[0].slides, ["A·2", "A·3", "A·4"]);
  assert.match(sections[0].md, /Rewritten\./);
  assert.match(sections[1].md, /A cache\./);
  const last = splitDoc(replaceSection(DOC, 1, "## TLB\n\nNew.")).sections[1];
  assert.match(last.md, /New\./);
  assert.throws(() => replaceSection(DOC, 5, "x"));
});
