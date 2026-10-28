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

// ---------- a notebook = several chapters (one per PDF), each a book of topics ----------

export type ChapterStatus = "new" | "generating" | "ready" | "failed";

/** One library chapter as the reader receives it: `md` is null until its doc for this level exists. */
export type ChapterInput = {
  id: string;
  slug: string;
  title: string;
  status: ChapterStatus;
  error?: string | null;
  md: string | null;
};

/** A section rewritten at another level (kept per notebook), keyed by `${chapter id}:${section}`. */
export type Variant = { level: string; body: string };

export type LibPage = BookPage & { chapterIdx: number; chapterId: string; slug: string; level?: string };

export type LibChapter = {
  id: string;
  slug: string;
  title: string;
  status: ChapterStatus;
  error: string | null;
  book: Book | null; // null while the chapter has no doc yet
  pages: LibPage[];
};

/** Reading order: Cover (index 0), then per chapter its cover followed by its topics. */
export type Entry = { kind: "chapter"; chapterIdx: number } | { kind: "topic"; chapterIdx: number; page: LibPage };

export type LibraryBook = {
  chapters: LibChapter[];
  entries: Entry[]; // entry i is shown as page i + 1
  slideCount: number;
  minutes: number;
  topics: number;
};

export function parseLibraryBook(inputs: ChapterInput[], variants: Record<string, Variant> = {}): LibraryBook {
  const chapters: LibChapter[] = inputs.map((c, chapterIdx) => {
    const book = c.md ? parseBook(c.md) : null;
    const pages: LibPage[] = (book?.pages ?? []).map((p) => {
      const v = variants[`${c.id}:${p.index}`];
      let page: BookPage = p;
      if (v) {
        const { title, body } = splitTitle(trimRules(v.body), p.title);
        page = { ...p, title, body, minutes: minutesFor(body) };
      }
      return { ...page, chapterIdx, chapterId: c.id, slug: c.slug, level: v?.level };
    });
    // the chapter's own title comes from its doc's heading once it exists
    return { id: c.id, slug: c.slug, title: book?.title ?? c.title, status: c.status, error: c.error ?? null, book, pages };
  });
  const entries: Entry[] = chapters.flatMap((c, chapterIdx) => [
    { kind: "chapter" as const, chapterIdx },
    ...c.pages.map((page) => ({ kind: "topic" as const, chapterIdx, page })),
  ]);
  return {
    chapters,
    entries,
    slideCount: chapters.reduce((n, c) => n + (c.book?.slideCount ?? 0), 0),
    minutes: chapters.reduce((n, c) => n + c.pages.reduce((m, p) => m + p.minutes, 0), 0),
    topics: chapters.reduce((n, c) => n + c.pages.length, 0),
  };
}

/** Where `?p=` / the stored place points: entry index (1-based page number), clamped. */
export function clampPage(n: number, book: LibraryBook): number {
  return Math.min(Math.max(0, Math.trunc(n) || 0), book.entries.length);
}

/** Page number of a chapter's cover, or of one of its topics. */
export function pageNumber(book: LibraryBook, chapterIdx: number, section?: number): number {
  const i = book.entries.findIndex((e) =>
    section === undefined
      ? e.kind === "chapter" && e.chapterIdx === chapterIdx
      : e.kind === "topic" && e.chapterIdx === chapterIdx && e.page.index === section,
  );
  return i < 0 ? 0 : i + 1;
}
