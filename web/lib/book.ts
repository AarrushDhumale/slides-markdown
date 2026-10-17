// A generated doc, read as a book: a cover (title, sources, overview, key takeaways) followed by one
// page per topic. Pages are the doc's `<!-- slides … -->` sections, so page i here is section i for
// the "Explain this" context on both sides (backend/pipeline/explain.py). Pure functions, no React.
import { splitDoc, stripContents } from "./slide-anchors.ts";

export const WORDS_PER_MINUTE = 230;

export type BookPage = {
  index: number; // section index (matches the worker's)
  number: number; // 1-based page number shown to the reader
  title: string;
  spec: string; // "A·4-6, B·2"
  slides: string[];
  body: string; // markdown without its title heading
  minutes: number;
};

export type Book = {
  title: string;
  decks: string[]; // source PDF names
  intro: string; // overview + key takeaways, markdown
  pages: BookPage[];
  slideCount: number;
  minutes: number;
};

export function minutesFor(text: string): number {
  return Math.max(1, Math.round(text.split(/\s+/).filter(Boolean).length / WORDS_PER_MINUTE));
}

/** Drop the `---` rules the assembler puts between sections. */
export function trimRules(md: string): string {
  return md.replace(/(\n\s*---\s*)+$/, "").replace(/^(\s*---\s*\n)+/, "").trim();
}

/** `## Title\n\nbody` -> { title, body }. A section without a heading keeps its text and gets a number. */
export function splitTitle(md: string, fallback: string): { title: string; body: string } {
  const m = md.match(/^#{1,6}[ \t]+(.+?)[ \t]*\n+/);
  if (!m) return { title: fallback, body: md };
  return { title: m[1].replace(/\*+/g, "").trim() || fallback, body: md.slice(m[0].length).trim() };
}

export function parseBook(md: string): Book {
  const { preamble, sections } = splitDoc(md);
  const title = preamble.match(/^# (.+)$/m)?.[1].trim() ?? "Notes";
  const sourcesLine = preamble.match(/\*\*Sources:\*\*\s*(.+)$/m)?.[1] ?? "";
  const decks = sourcesLine
    .split(/,\s*(?=\*\*[A-Z]+\*\*\s*=)/)
    .map((s) => s.replace(/\*\*[A-Z]+\*\*\s*=\s*/, "").trim())
    .filter(Boolean);
  const intro = trimRules(stripContents(preamble).replace(/^# .+\n*/m, ""));

  const pages: BookPage[] = sections.map((s, i) => {
    const { title: pageTitle, body } = splitTitle(trimRules(s.md), `Topic ${i + 1}`);
    return {
      index: s.index,
      number: i + 1,
      title: pageTitle,
      spec: s.spec,
      slides: s.slides,
      body,
      minutes: minutesFor(body),
    };
  });
  return {
    title,
    decks,
    intro,
    pages,
    slideCount: new Set(pages.flatMap((p) => p.slides)).size,
    minutes: pages.reduce((n, p) => n + p.minutes, 0) || minutesFor(md),
  };
}
