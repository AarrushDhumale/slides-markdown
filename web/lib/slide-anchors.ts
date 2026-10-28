// Splitting a generated doc the same way the Python worker does (backend/pipeline/explain.py), so a
// section index means the same thing on both sides: section i = text after the i-th
// `<!-- slides A·4-6, B·2 -->` anchor, up to the next one.

export const SEP = "·";
const ANCHOR_RE = /<!-- slides ([^>]*?) -->/g;

export type DocSection = { index: number; spec: string; slides: string[]; md: string };

export function expandIds(spec: string): string[] {
  const out: string[] = [];
  for (const raw of spec.split(",")) {
    const m = raw.trim().match(new RegExp(`^([A-Z]+)${SEP}(\\d+)(?:-(\\d+))?$`));
    if (!m) continue;
    const a = Number(m[2]);
    const b = Number(m[3] ?? m[2]);
    for (let p = a; p <= b; p++) out.push(`${m[1]}${SEP}${p}`);
  }
  return out;
}

export function splitDoc(md: string): { preamble: string; sections: DocSection[] } {
  const marks = [...md.matchAll(ANCHOR_RE)];
  const preamble = marks.length ? md.slice(0, marks[0].index) : md;
  const sections = marks.map((m, i) => {
    const start = (m.index ?? 0) + m[0].length;
    const end = i + 1 < marks.length ? marks[i + 1].index ?? md.length : md.length;
    return { index: i, spec: m[1], slides: expandIds(m[1]), md: md.slice(start, end).trim() };
  });
  return { preamble: preamble.trim(), sections };
}

/** The doc with section `idx` replaced by `body`; its anchor and the rule before the next section stay,
 * so section indexes don't move (same as backend/pipeline/explain.py:replace_section). */
export function replaceSection(md: string, idx: number, body: string): string {
  const marks = [...md.matchAll(ANCHOR_RE)];
  const mark = marks[idx];
  if (!mark) throw new Error(`Section ${idx} not found (doc has ${marks.length})`);
  const next = marks[idx + 1];
  const start = (mark.index ?? 0) + mark[0].length;
  const tail = next ? "\n\n---\n\n" : "\n";
  return md.slice(0, start) + "\n" + body.trim() + tail + md.slice(next ? next.index : md.length);
}

/** `B·12` -> `B-012`, the worker's file id for slide images. */
export function slideFileId(sid: string): string {
  const [letter, page] = sid.split(SEP);
  return `${letter}-${page.padStart(3, "0")}`;
}

/** Same slug as backend/pipeline/assemble.py:anchor, so contents links match heading ids. */
export function anchor(title: string): string {
  return title.trim().toLowerCase().replace(/[^\p{L}\p{N}_\s-]/gu, "").replace(/\s/g, "-");
}

export type TocEntry = { level: 2 | 3; title: string; id: string };

/** Chapter (##) and part (###) headings in document order, skipping the doc's own Contents list. */
export function toc(md: string): TocEntry[] {
  const out: TocEntry[] = [];
  let inFence = false;
  for (const line of md.split("\n")) {
    if (/^\s*(```|~~~)/.test(line)) inFence = !inFence;
    if (inFence) continue;
    const m = line.match(/^(##|###) (.+)$/);
    if (!m || /^contents$/i.test(m[2].trim())) continue;
    const title = m[2].trim();
    out.push({ level: m[1].length as 2 | 3, title, id: anchor(title) });
  }
  return out;
}

/** Drop what the reader shows itself: the doc's "## Contents" list (contents menu), the
 * "> **Level:** …" line (toolbar) and slide anchors (slide chips). */
export function stripContents(md: string): string {
  return md
    .replace(/^## Contents\n[\s\S]*?(?=\n---|\n## |\n<!--|(?![\s\S]))/m, "")
    .replace(/^> \*\*Level:\*\*.*\n?/m, "")
    .replace(ANCHOR_RE, "");
}

/** Asset paths referenced as images: `![..](assets/A-003-f1.png)`. */
export function assetPaths(md: string): string[] {
  return [...new Set([...md.matchAll(/\]\((assets\/[\w.-]+\.(?:png|webp|jpg))\)/g)].map((m) => m[1]))];
}
