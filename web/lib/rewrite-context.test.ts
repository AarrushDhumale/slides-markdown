import assert from "node:assert/strict";
import { test } from "node:test";

import { buildRewriteMessages, cleanSection, isRewriteLevel, RewriteError, targetWords } from "./rewrite-context.ts";

const DOC = `# Paging

<!-- slides A·2-3 -->
## Paging basics

Pages map to frames. ![diagram of a page table](assets/A-002-f1.png)

---

<!-- slides A·4 -->
## TLB

A cache of translations.
`;
const EXTRACTS = {
  "A·2": { slide_role: "content", title: "Paging", bullets: ["page = fixed-size block", "frame = physical block"], figures: [{ description: "x" }] },
  "A·3": { slide_role: "content", title: "Page table", bullets: ["maps page to frame"], equations: ["a=b"] },
  "A·4": { slide_role: "content", title: "TLB", bullets: ["caches PTEs"] },
};

test("rewrite messages: writer prompt, outline marker, only this section's slides, figures to keep", () => {
  const { messages, section } = buildRewriteMessages(DOC, EXTRACTS, 0, "proficient");
  assert.equal(section, 0);
  assert.equal(messages[0].role, "system");
  assert.match(messages[0].content, /Level: Proficient|proficient/i);
  const user = messages[1].content;
  assert.match(user, /-> 1\. Paging basics/);
  assert.match(user, /\n {3}2\. TLB/);
  assert.match(user, /### Slide A·2/);
  assert.match(user, /page = fixed-size block/);
  assert.doesNotMatch(user, /caches PTEs/);
  assert.doesNotMatch(user, /slide_role/);
  assert.match(user, /assets\/A-002-f1\.png: diagram of a page table/);
  assert.match(user, /Heading to use: ## Paging basics/);
});

test("budget scales with the level and stays within bounds", () => {
  const ids = ["A·2", "A·3"];
  assert.ok(targetWords("donkey", ids, EXTRACTS) >= targetWords("proficient", ids, EXTRACTS));
  assert.equal(targetWords("learner", ids, EXTRACTS), 120);
});

test("bad section and levels are rejected", () => {
  assert.throws(() => buildRewriteMessages(DOC, EXTRACTS, 7, "learner"), RewriteError);
  assert.throws(() => buildRewriteMessages(DOC, {}, 0, "learner"), RewriteError);
  assert.equal(isRewriteLevel("summary"), false);
  assert.equal(isRewriteLevel("donkey"), true);
});

test("cleanSection strips fences and anchors and guarantees a ## heading", () => {
  assert.equal(cleanSection("```markdown\n## T\n\nbody\n```", "X"), "## T\n\nbody");
  assert.equal(cleanSection("<!-- slides A·2 -->\n# Big\n\nbody", "X"), "## Big\n\nbody");
  assert.equal(cleanSection("just text", "Fallback"), "## Fallback\n\njust text");
});
