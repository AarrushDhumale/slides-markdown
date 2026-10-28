"use client";

import * as Dialog from "@radix-ui/react-dialog";
import * as Popover from "@radix-ui/react-popover";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Check,
  ChevronRight,
  Download,
  GalleryHorizontalEnd,
  ListTree,
  Loader2,
  NotebookPen,
  Printer,
  ScrollText,
  Sparkles,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ExplainPanel, type ExplainTarget } from "@/components/explain-panel";
import { Markdown, type ImageHandlers } from "@/components/markdown";
import { NotesPanel, type NotesApi, type NoteTarget } from "@/components/notes-panel";
import { SectionLevelMenu } from "@/components/section-level-menu";
import { Button } from "@/components/ui/button";
import { pageNumber, type Entry, type LibChapter, type LibPage, type LibraryBook } from "@/lib/book";
import { supabase } from "@/lib/client";
import { cn } from "@/lib/cn";
import { demoDir } from "@/lib/demo";
import { sectionKey } from "@/lib/library";
import type { RewriteLevel } from "@/lib/limits";
import { assetPaths } from "@/lib/slide-anchors";

export type SlideIndex = Record<string, { image: string; title: string }>;
type Mode = "book" | "scroll";

const LEVEL_NAMES: Record<string, string> = {
  summary: "Summary",
  notes: "Lecture notes",
  learner: "Learner notes",
  proficient: "Revision sheet",
  donkey: "Explain simply",
};

/**
 * Signed URLs for generated files of library chapters. Paths are `<chapter slug>/assets/x.png` or
 * `<chapter slug>/slides/A-004.webp`; the result is keyed by the same path. Cached.
 */
export function useSignedUrls(notebookId: string) {
  const [urls, setUrls] = useState<Record<string, string>>({});
  const pending = useRef(new Set<string>());
  const request = useCallback(
    async (paths: string[]) => {
      const todo = paths.filter((p) => !pending.current.has(p));
      if (!todo.length) return;
      todo.forEach((p) => pending.current.add(p));
      if (notebookId === "demo") {
        // the demo has one chapter whose files sit directly in the demo folder
        setUrls((u) => ({ ...u, ...Object.fromEntries(todo.map((p) => [p, `/${demoDir()}/${p.split("/").slice(1).join("/")}`])) }));
        return;
      }
      const { data } = await supabase().storage.from("library").createSignedUrls(todo, 3600);
      const next: Record<string, string> = {};
      data?.forEach((d, i) => d.signedUrl && (next[todo[i]] = d.signedUrl));
      setUrls((u) => ({ ...u, ...next }));
    },
    [notebookId],
  );
  return { urls, request };
}

function stored<T>(key: string, fallback: T, parse: (raw: string) => T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : parse(raw);
  } catch {
    return fallback;
  }
}
function save(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode: position just won't be remembered */
  }
}

/** Stable identity of a page (page numbers shift while chapters are still arriving). */
const entryKey = (e: Entry, book: LibraryBook) => (e.kind === "chapter" ? `c:${book.chapters[e.chapterIdx].slug}` : `t:${e.page.slug}:${e.page.index}`);
const readKey = (p: LibPage) => `${p.slug}:${p.index}`;

function composeMarkdown(title: string, book: LibraryBook): string {
  return [
    `# ${title}`,
    ...book.chapters.flatMap((c) =>
      c.book ? [`## ${c.title}`, c.book.intro, ...c.pages.map((p) => `### ${p.title}\n\n${p.body}`)] : [`## ${c.title}\n\n_Not generated yet._`],
    ),
  ]
    .filter(Boolean)
    .join("\n\n");
}

export function Reader({
  notebookId,
  notebookTitle,
  level,
  book,
  slides,
  rewriting,
  notes,
  onChoose,
  onGenerate,
}: {
  notebookId: string;
  notebookTitle: string;
  level: string;
  book: LibraryBook;
  slides: Record<string, SlideIndex | null>; // by chapter slug
  rewriting: string | null; // sectionKey of the topic being rewritten
  notes: NotesApi;
  onChoose: (chapterId: string, section: number, level: RewriteLevel | null) => void;
  onGenerate: () => void;
}) {
  const { entries } = book;
  const total = entries.length;
  const hasPages = total > 0;
  const keys = useMemo(() => entries.map((e) => entryKey(e, book)), [entries, book]);
  const { urls, request } = useSignedUrls(notebookId);

  // Mounted only after the notes were fetched in the browser, so reading localStorage here is safe.
  const [mode, setMode] = useState<Mode>(() => stored(`mode:${notebookId}`, "book", (r) => (r === "scroll" ? "scroll" : "book")));
  const [at, setAt] = useState<string>(() => {
    const q = Number(new URLSearchParams(location.search).get("p")); // ?p=3 opens page 3 (handy for links and screenshots)
    if (q > 0 && q <= keys.length) return keys[q - 1];
    return stored(`page:${notebookId}`, "cover", (r) => r);
  });
  const [read, setRead] = useState<Set<string>>(() =>
    stored(`read:${notebookId}`, new Set<string>(), (r) => new Set<string>((JSON.parse(r) as unknown[]).filter((x): x is string => typeof x === "string"))),
  );
  const [dir, setDir] = useState<"next" | "prev">("next");
  const [printing, setPrinting] = useState(false);
  const [zoom, setZoom] = useState<{ url: string; alt: string } | null>(null);
  const [target, setTarget] = useState<ExplainTarget | null>(null);
  const [pick, setPick] = useState<ExplainTarget | null>(null); // floating "Explain" button for a text selection
  const [notesOpen, setNotesOpen] = useState(false);
  const [scrollNote, setScrollNote] = useState<NoteTarget | null>(null);
  const container = useRef<HTMLDivElement>(null);

  const page = at === "cover" ? 0 : keys.indexOf(at) + 1; // a place that no longer exists falls back to the cover
  const entry = page > 0 ? entries[page - 1] : null;
  const showAll = mode === "scroll" || printing || !hasPages;

  const go = useCallback(
    (n: number) => {
      const next = Math.min(Math.max(0, n), total);
      setDir(next >= page ? "next" : "prev");
      setAt(next === 0 ? "cover" : keys[next - 1]);
      setTarget(null);
      setPick(null);
      window.scrollTo({ top: 0 });
    },
    [page, total, keys],
  );

  useEffect(() => {
    save(`page:${notebookId}`, at);
    if (entry?.kind === "topic") {
      const k = readKey(entry.page);
      setRead((prev) => {
        if (prev.has(k)) return prev;
        const next = new Set(prev).add(k);
        save(`read:${notebookId}`, JSON.stringify([...next]));
        return next;
      });
    }
  }, [at, entry, notebookId]);

  const setViewMode = (m: Mode) => {
    setMode(m);
    save(`mode:${notebookId}`, m);
    setTarget(null);
    setPick(null);
    window.scrollTo({ top: 0 });
  };

  // Images: only this page and the next one in book mode; everything when scrolling or printing.
  useEffect(() => {
    const assets = (e: Entry | undefined) => {
      if (!e) return [];
      const c = book.chapters[e.chapterIdx];
      const text = e.kind === "topic" ? e.page.body : (c.book?.intro ?? "");
      return assetPaths(text).map((p) => `${c.slug}/${p}`);
    };
    if (showAll) return void request(entries.flatMap((e) => assets(e)));
    request([...assets(entries[page - 1]), ...assets(entries[page])]);
  }, [showAll, entries, page, book, request]);

  // Arrow keys turn pages (not while typing, or while a dialog such as Explain is open).
  useEffect(() => {
    if (showAll) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
      const el = document.activeElement as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)) return;
      if (document.querySelector('[role="dialog"]')) return;
      go(page + (e.key === "ArrowRight" ? 1 : -1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showAll, page, go]);

  // Print everything, not just the open page.
  useEffect(() => {
    if (!printing) return;
    const done = () => setPrinting(false);
    window.addEventListener("afterprint", done, { once: true });
    const t = setTimeout(() => window.print(), 900);
    return () => {
      clearTimeout(t);
      window.removeEventListener("afterprint", done);
    };
  }, [printing]);

  const imageHandlers = useCallback(
    (p: LibPage): ImageHandlers => ({
      resolve: (src) => urls[`${p.slug}/${src}`],
      onZoom: (url, alt) => setZoom({ url, alt }),
      onExplain: (src) => {
        const el = container.current?.querySelector(`img[src="${CSS.escape(urls[`${p.slug}/${src}`] ?? "")}"]`);
        const r = el?.getBoundingClientRect();
        setTarget({ chapterId: p.chapterId, section: p.index, image: src, label: "This figure", rect: r && { x: r.x, y: r.y, w: r.width, h: Math.min(r.height, 80) } });
      },
    }),
    [urls],
  );
  const plainImages = useCallback((slug: string): ImageHandlers => ({ resolve: (s) => urls[`${slug}/${s}`], onZoom: (url, alt) => setZoom({ url, alt }) }), [urls]);

  // text selection -> floating Explain button
  useEffect(() => {
    const onUp = () => {
      setTimeout(() => {
        const sel = window.getSelection();
        const text = sel?.toString().trim() ?? "";
        if (!sel || sel.rangeCount === 0 || text.length < 3 || text.length > 1500) return setPick(null);
        const node = sel.anchorNode instanceof Element ? sel.anchorNode : sel.anchorNode?.parentElement;
        const sec = node?.closest<HTMLElement>("[data-section][data-chapter]");
        if (!sec || !container.current?.contains(sec)) return setPick(null);
        const r = sel.getRangeAt(0).getBoundingClientRect();
        setPick({ chapterId: sec.dataset.chapter!, section: Number(sec.dataset.section), selection: text, rect: { x: r.x, y: r.y, w: r.width, h: r.height } });
      }, 0);
    };
    const onScroll = () => setPick(null);
    document.addEventListener("mouseup", onUp);
    document.addEventListener("touchend", onUp);
    window.addEventListener("scroll", onScroll, { passive: true, capture: true });
    return () => {
      document.removeEventListener("mouseup", onUp);
      document.removeEventListener("touchend", onUp);
      window.removeEventListener("scroll", onScroll, { capture: true });
    };
  }, []);

  const download = () => {
    const blob = new Blob([composeMarkdown(notebookTitle, book)], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${notebookTitle.replace(/[^\w -]+/g, "").trim() || "notes"} - ${LEVEL_NAMES[level] ?? level}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const explainSlide = (p: LibPage, sid: string, rect?: ExplainTarget["rect"]) =>
    setTarget({ chapterId: p.chapterId, section: p.index, image: `slide:${sid}`, label: `Slide ${sid}: ${slides[p.slug]?.[sid]?.title ?? ""}`, rect });

  // ----- notes: the open topic in book mode, or the topic whose Notes button was pressed when scrolling -----
  const noteTarget = (p: LibPage): NoteTarget => ({ chapterId: p.chapterId, section: p.index, chapterTitle: book.chapters[p.chapterIdx].title, title: p.title });
  const currentTopic = entry?.kind === "topic" ? entry.page : null;
  const activeNote: NoteTarget | null = showAll ? scrollNote : currentTopic ? noteTarget(currentTopic) : null;
  const notesVisible = notesOpen && !!activeNote && !printing;

  const tools = (p: LibPage) => {
    const slideIndex = slides[p.slug];
    const hasNote = !!notes.notes[sectionKey(p.chapterId, p.index)];
    return (
      <div className="flex flex-wrap items-center justify-end gap-2">
        {slideIndex && p.slides.length > 0 && (
          <SlidesChip spec={p.spec} ids={p.slides} slides={slideIndex} slug={p.slug} urls={urls} request={request} onExplain={(sid, rect) => explainSlide(p, sid, rect)} />
        )}
        <SectionLevelMenu level={p.level} busy={rewriting === sectionKey(p.chapterId, p.index)} disabled={!!rewriting} onChoose={(l) => onChoose(p.chapterId, p.index, l)} />
        <button
          onClick={() => {
            if (showAll) {
              setScrollNote(noteTarget(p));
              setNotesOpen(true);
            } else setNotesOpen((o) => !o);
          }}
          aria-pressed={notesVisible && activeNote?.chapterId === p.chapterId && activeNote.section === p.index}
          title="Write notes on this topic"
          className={cn(
            "relative inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 font-sans text-[11.5px] font-medium shadow-card transition",
            hasNote ? "border-accent/50 bg-accent-soft text-accent-text" : "border-border bg-surface text-muted hover:text-text",
          )}
        >
          <NotebookPen className="size-3.5" /> Notes
        </button>
      </div>
    );
  };

  const levelName = LEVEL_NAMES[level] ?? level;
  const progress = hasPages && !showAll ? page / total : 0;
  const position = !hasPages
    ? ""
    : showAll
      ? `· ${book.minutes} min read`
      : !entry
        ? "· Cover"
        : entry.kind === "chapter"
          ? `· Chapter ${entry.chapterIdx + 1} of ${book.chapters.length}`
          : `· ${book.chapters[entry.chapterIdx].title}`;

  return (
    <div ref={container} className="relative">
      <div className="no-print sticky top-14 z-20 -mx-4 mb-8 flex items-center gap-2 border-b border-border bg-bg/90 px-4 py-2 backdrop-blur sm:-mx-6 sm:px-6">
        <span className="text-[13px] font-semibold whitespace-nowrap">{levelName}</span>
        <span className="truncate text-[12.5px] text-faint max-sm:hidden">{position}</span>
        <div className="ml-auto flex items-center gap-1">
          {hasPages && (
            <div role="group" aria-label="Reading view" className="mr-1 flex rounded-lg bg-surface-2 p-0.5">
              {([["book", BookOpen, "Book"], ["scroll", ScrollText, "Scroll"]] as const).map(([m, Icon, label]) => (
                <button
                  key={m}
                  aria-pressed={mode === m}
                  onClick={() => setViewMode(m)}
                  title={`${label} view`}
                  className={cn(
                    "flex h-7 items-center gap-1.5 rounded-md px-2 text-[12px] font-medium transition-colors",
                    mode === m ? "bg-surface text-text shadow-card" : "text-muted hover:text-text",
                  )}
                >
                  <Icon className="size-3.5" /> <span className="max-md:hidden">{label}</span>
                </button>
              ))}
            </div>
          )}
          {hasPages && !showAll && (
            <Button
              size="sm"
              variant="ghost"
              disabled={!currentTopic}
              aria-pressed={notesVisible}
              onClick={() => setNotesOpen((o) => !o)}
              title={currentTopic ? "My notes on this topic" : "Open a topic to take notes"}
              className={cn(notesVisible && "bg-accent-soft text-accent-text")}
            >
              <NotebookPen className="size-4" /> <span className="max-sm:hidden">Notes</span>
            </Button>
          )}
          {hasPages && (
            <Popover.Root>
              <Popover.Trigger asChild>
                <Button size="sm" variant="ghost" aria-label="Contents" title="Contents">
                  <ListTree className="size-4" /> <span className="max-sm:hidden">Contents</span>
                </Button>
              </Popover.Trigger>
              <Popover.Portal>
                <Popover.Content align="end" sideOffset={6} className="rise z-50 max-h-[65dvh] w-[340px] max-w-[calc(100vw-24px)] overflow-y-auto rounded-xl border border-border bg-surface p-1.5 shadow-pop">
                  <Popover.Close asChild>
                    <button
                      onClick={() => (mode === "book" ? go(0) : window.scrollTo({ top: 0, behavior: "smooth" }))}
                      className={cn("flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] hover:bg-surface-2", !showAll && page === 0 && "bg-accent-soft text-accent-text")}
                    >
                      <BookOpen className="size-3.5 shrink-0" /> Cover &amp; overview
                    </button>
                  </Popover.Close>
                  {entries.map((e, i) => {
                    const n = i + 1;
                    const c = book.chapters[e.chapterIdx];
                    const open = () => {
                      if (mode === "book") go(n);
                      else document.getElementById(e.kind === "chapter" ? `chapter-${c.slug}` : `topic-${e.page.slug}-${e.page.index}`)?.scrollIntoView({ behavior: "smooth" });
                    };
                    return (
                      <Popover.Close asChild key={keys[i]}>
                        {e.kind === "chapter" ? (
                          <button onClick={open} className={cn("mt-1 flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[12.5px] font-semibold tracking-tight hover:bg-surface-2", !showAll && page === n && "bg-accent-soft text-accent-text")}>
                            <span className="min-w-0 flex-1 truncate">{c.title}</span>
                            <ChapterPill chapter={c} />
                          </button>
                        ) : (
                          <button onClick={open} className={cn("flex w-full items-center gap-2.5 rounded-lg py-1.5 pr-2.5 pl-4 text-left text-[13px] hover:bg-surface-2", !showAll && page === n && "bg-accent-soft text-accent-text")}>
                            <span className={cn("grid size-5 shrink-0 place-items-center rounded-md text-[11px] font-semibold tabular-nums", read.has(readKey(e.page)) ? "bg-success-soft text-success" : "bg-surface-2 text-muted")}>
                              {read.has(readKey(e.page)) ? <Check className="size-3" /> : e.page.number}
                            </span>
                            <span className="min-w-0 flex-1 truncate">{e.page.title}</span>
                          </button>
                        )}
                      </Popover.Close>
                    );
                  })}
                </Popover.Content>
              </Popover.Portal>
            </Popover.Root>
          )}
          <Button size="sm" variant="ghost" onClick={download} title="Download Markdown">
            <Download className="size-4" /> <span className="max-sm:hidden">.md</span>
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setPrinting(true)} title="Print or save as PDF (all topics)">
            <Printer className="size-4" />
          </Button>
        </div>
        {hasPages && !showAll && (
          <div className="absolute inset-x-0 bottom-[-1px] h-0.5 bg-transparent" aria-hidden>
            <div className="h-full bg-accent/70" style={{ width: `${progress * 100}%` }} />
          </div>
        )}
      </div>

      <div className={cn("grid w-full gap-x-8 lg:grid-cols-[220px_minmax(0,1fr)]", notesVisible ? "xl:grid-cols-[220px_minmax(0,1fr)_300px]" : "xl:grid-cols-[220px_minmax(0,1fr)_200px]")}>
        <DocsNav
          book={book}
          page={showAll ? -1 : page}
          read={read}
          notes={notes.notes}
          onOpen={(n) => {
            if (mode === "book") go(n);
            else if (n === 0) window.scrollTo({ top: 0, behavior: "smooth" });
            else {
              const e = entries[n - 1];
              document.getElementById(e.kind === "chapter" ? `chapter-${book.chapters[e.chapterIdx].slug}` : `topic-${e.page.slug}-${e.page.index}`)?.scrollIntoView({ behavior: "smooth" });
            }
          }}
        />
        <div className="mx-auto w-full min-w-0 max-w-[44rem]">
          {showAll ? (
            <article className="max-w-[44rem] pb-32">
              <h1 className="font-sans text-[2.1rem] leading-[1.12] font-semibold tracking-[-0.03em] text-balance">{notebookTitle}</h1>
              {book.chapters.map((c, ci) => (
                <div key={c.id}>
                  <section id={`chapter-${c.slug}`} className={cn("scroll-mt-28 pt-12", ci > 0 && "print:break-before-page")}>
                    <p className="eyebrow">Chapter {ci + 1}</p>
                    <h2 className="mt-2 mb-4 font-sans text-[1.6rem] font-semibold tracking-tight">{c.title}</h2>
                    {c.book ? (
                      c.book.intro && <Markdown images={plainImages(c.slug)}>{c.book.intro}</Markdown>
                    ) : (
                      <Placeholder chapter={c} onGenerate={onGenerate} />
                    )}
                  </section>
                  {c.pages.map((p) => (
                    <section key={p.index} id={`topic-${p.slug}-${p.index}`} data-section={p.index} data-chapter={p.chapterId} className="scroll-mt-28">
                      <div className="no-print flex justify-end pt-8 pb-1">{tools(p)}</div>
                      <Markdown images={imageHandlers(p)}>{`### ${p.title}\n\n${p.body}`}</Markdown>
                    </section>
                  ))}
                </div>
              ))}
            </article>
          ) : !entry ? (
            <Cover book={book} title={notebookTitle} levelName={levelName} read={read} onOpen={go} onGenerate={onGenerate} />
          ) : entry.kind === "chapter" ? (
            <ChapterCover
              key={at}
              chapter={book.chapters[entry.chapterIdx]}
              index={entry.chapterIdx}
              count={book.chapters.length}
              book={book}
              read={read}
              images={plainImages(book.chapters[entry.chapterIdx].slug)}
              onOpen={go}
              onGenerate={onGenerate}
            />
          ) : (
            <TopicPage
              key={at}
              page={entry.page}
              chapter={book.chapters[entry.chapterIdx]}
              count={book.chapters[entry.chapterIdx].pages.length}
              prev={page > 1 ? entries[page - 2] : null}
              next={page < total ? entries[page] : null}
              book={book}
              images={imageHandlers(entry.page)}
              tools={tools(entry.page)}
              onGo={go}
              dir={dir}
            />
          )}
        </div>
        {notesVisible && activeNote ? (
          <div className="no-print max-xl:fixed max-xl:inset-x-3 max-xl:bottom-3 max-xl:z-40 xl:sticky xl:top-[112px] xl:h-[calc(100dvh-136px)] xl:self-start">
            <NotesPanel notebookId={notebookId} target={activeNote} api={notes} onClose={() => setNotesOpen(false)} />
          </div>
        ) : !showAll && entry?.kind === "topic" ? (
          <OnThisPage root={container} depKey={at} />
        ) : (
          <div className="max-xl:hidden" />
        )}
      </div>

      {pick && !target && (
        <button
          type="button"
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => {
            setTarget(pick);
            setPick(null);
          }}
          style={{ left: Math.min(pick.rect!.x + pick.rect!.w / 2 - 50, window.innerWidth - 116), top: Math.max(8, pick.rect!.y - 44) }}
          className="no-print rise fixed z-40 inline-flex h-9 items-center gap-1.5 rounded-full bg-text px-3.5 text-[13px] font-medium text-bg shadow-pop"
        >
          <Sparkles className="size-3.5" /> Explain
        </button>
      )}

      {target && <ExplainPanel notebookId={notebookId} level={level} target={target} onClose={() => setTarget(null)} />}

      <Dialog.Root open={!!zoom} onOpenChange={(o) => !o && setZoom(null)}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm" />
          <Dialog.Content className="fixed inset-4 z-50 flex flex-col items-center justify-center gap-3 outline-none sm:inset-10" onClick={() => setZoom(null)}>
            <Dialog.Title className="sr-only">{zoom?.alt || "Figure"}</Dialog.Title>
            {zoom && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={zoom.url} alt={zoom.alt} className="max-h-full max-w-full rounded-xl bg-white object-contain shadow-pop" />
            )}
            {zoom?.alt && <p className="max-w-2xl text-center text-sm text-white/85">{zoom.alt}</p>}
            <Dialog.Close className="absolute top-0 right-0 grid size-10 place-items-center rounded-full bg-white/10 text-white hover:bg-white/20" aria-label="Close">
              <X className="size-5" />
            </Dialog.Close>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}

/** Where a chapter is in its life: shown next to its title in the contents and navigation. */
function ChapterPill({ chapter }: { chapter: LibChapter }) {
  if (chapter.book) return null;
  if (chapter.status === "generating")
    return (
      <span className="inline-flex shrink-0 items-center gap-1 text-[11px] font-medium text-accent-text">
        <Loader2 className="size-3 animate-spin" /> Writing
      </span>
    );
  if (chapter.status === "failed")
    return (
      <span className="inline-flex shrink-0 items-center gap-1 text-[11px] font-medium text-danger">
        <AlertTriangle className="size-3" /> Failed
      </span>
    );
  return <span className="shrink-0 text-[11px] font-medium text-faint">Waiting</span>;
}

/** What a chapter without a doc at this level shows instead of its topics. */
function Placeholder({ chapter, onGenerate }: { chapter: LibChapter; onGenerate: () => void }) {
  const failed = chapter.status === "failed";
  const writing = chapter.status === "generating";
  return (
    <div className={cn("rounded-xl border p-5", failed ? "border-danger/30 bg-danger-soft" : "border-border bg-surface")}>
      <div className={cn("flex items-center gap-2 text-[14px] font-semibold", failed && "text-danger")}>
        {writing ? <Loader2 className="size-4 animate-spin text-accent" /> : failed ? <AlertTriangle className="size-4" /> : null}
        {writing ? "Writing this chapter now" : failed ? "This chapter failed" : "Not written at this level yet"}
      </div>
      <p className="mt-1.5 text-[13px] text-muted">
        {writing
          ? "Every slide is read first, then the topics appear here as soon as they are written. You can read the other chapters meanwhile."
          : failed
            ? (chapter.error ?? "Something went wrong while writing it.")
            : "It will be written when the generator picks it up."}
      </p>
      {!writing && (
        <Button size="sm" variant="secondary" className="mt-3" onClick={onGenerate}>
          {failed ? "Try again" : "Write it now"}
        </Button>
      )}
    </div>
  );
}

function TopicList({ chapter, book, read, onOpen }: { chapter: LibChapter; book: LibraryBook; read: Set<string>; onOpen: (n: number) => void }) {
  return (
    <ol className="overflow-hidden rounded-xl border border-border">
      {chapter.pages.map((p) => (
        <li key={p.index} className="border-b border-border last:border-0">
          <button onClick={() => onOpen(pageNumber(book, p.chapterIdx, p.index))} className="group flex w-full items-center gap-3.5 px-4 py-3 text-left transition-colors hover:bg-surface sm:px-5">
            <span className={cn("grid size-7 shrink-0 place-items-center rounded-lg text-[12px] font-semibold tabular-nums", read.has(readKey(p)) ? "bg-success-soft text-success" : "bg-surface-2 text-muted group-hover:bg-accent-soft group-hover:text-accent-text")}>
              {read.has(readKey(p)) ? <Check className="size-3.5" /> : p.number}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[14.5px] font-medium tracking-tight">{p.title}</span>
              <span className="block text-[12px] text-faint">
                Slides {p.spec.replace(/-/g, "–")} · {p.minutes} min{p.level ? " · rewritten" : ""}
              </span>
            </span>
            <ChevronRight className="size-4 shrink-0 text-faint transition-transform group-hover:translate-x-0.5 group-hover:text-text" />
          </button>
        </li>
      ))}
    </ol>
  );
}

function Cover({ book, title, levelName, read, onOpen, onGenerate }: { book: LibraryBook; title: string; levelName: string; read: Set<string>; onOpen: (n: number) => void; onGenerate: () => void }) {
  const ready = book.chapters.filter((c) => c.book).length;
  const meta = [
    `${book.chapters.length} chapter${book.chapters.length === 1 ? "" : "s"}`,
    `${book.topics} topic${book.topics === 1 ? "" : "s"}`,
    book.slideCount ? `${book.slideCount} slide${book.slideCount === 1 ? "" : "s"}` : null,
    book.minutes ? `${book.minutes} min read` : null,
  ].filter(Boolean);
  const topics = book.entries.flatMap((e, i) => (e.kind === "topic" ? [{ n: i + 1, p: e.page }] : []));
  const firstUnread = topics.find((t) => !read.has(readKey(t.p)))?.n ?? topics[0]?.n ?? (book.entries.length ? 1 : 0);
  const started = topics.some((t) => read.has(readKey(t.p)));
  return (
    <div className="max-w-[44rem] pb-24">
      <p className="eyebrow">{levelName}</p>
      <h1 className="mt-3 font-sans text-[2.1rem] leading-[1.12] font-semibold tracking-[-0.03em] text-balance sm:text-[2.5rem]">{title}</h1>
      <p className="mt-4 text-[13.5px] text-muted">{meta.join(" · ")}</p>
      {ready < book.chapters.length && (
        <p className="mt-1 flex items-center gap-1.5 text-[12.5px] text-faint">
          {book.chapters.some((c) => c.status === "generating") && <Loader2 className="size-3 animate-spin" />}
          {ready} of {book.chapters.length} chapters ready. The rest appear here as they are written.
        </p>
      )}
      {firstUnread > 0 && (
        <Button variant="primary" className="mt-8" onClick={() => onOpen(firstUnread)}>
          {started ? "Continue reading" : "Start reading"} <ArrowRight className="size-4" />
        </Button>
      )}

      <h2 className="eyebrow mt-12 mb-3">Contents</h2>
      <div className="space-y-6">
        {book.chapters.map((c, ci) => (
          <section key={c.id}>
            <button onClick={() => onOpen(pageNumber(book, ci))} className="mb-2 flex w-full items-center gap-2 text-left">
              <span className="text-[11.5px] font-semibold tracking-wider text-faint uppercase">Chapter {ci + 1}</span>
              <span className="min-w-0 flex-1 truncate text-[15px] font-semibold tracking-tight hover:underline">{c.title}</span>
              <ChapterPill chapter={c} />
            </button>
            {c.book ? <TopicList chapter={c} book={book} read={read} onOpen={onOpen} /> : <Placeholder chapter={c} onGenerate={onGenerate} />}
          </section>
        ))}
      </div>
    </div>
  );
}

function ChapterCover({ chapter, index, count, book, read, images, onOpen, onGenerate }: { chapter: LibChapter; index: number; count: number; book: LibraryBook; read: Set<string>; images: ImageHandlers; onOpen: (n: number) => void; onGenerate: () => void }) {
  const first = chapter.pages.find((p) => !read.has(readKey(p))) ?? chapter.pages[0];
  const doc = chapter.book;
  const meta = doc
    ? [`${chapter.pages.length} topic${chapter.pages.length === 1 ? "" : "s"}`, `${doc.slideCount} slides`, `${chapter.pages.reduce((n, p) => n + p.minutes, 0)} min read`]
    : [];
  return (
    <div className="max-w-[44rem] pb-24">
      <p className="eyebrow">
        Chapter {index + 1} <span className="opacity-70">of {count}</span>
      </p>
      <h1 className="mt-3 font-sans text-[2.1rem] leading-[1.12] font-semibold tracking-[-0.03em] text-balance sm:text-[2.5rem]">{chapter.title}</h1>
      {doc ? (
        <>
          <p className="mt-4 text-[13.5px] text-muted">{meta.join(" · ")}</p>
          {doc.decks.length > 0 && <p className="mt-1 line-clamp-2 text-[12.5px] text-faint">From {doc.decks.join(" · ")}</p>}
          {doc.intro && <Markdown images={images} className="mt-8">{doc.intro}</Markdown>}
          {first && (
            <Button variant="primary" className="mt-8" onClick={() => onOpen(pageNumber(book, index, first.index))}>
              Start this chapter <ArrowRight className="size-4" />
            </Button>
          )}
          <h2 className="eyebrow mt-12 mb-3">Topics</h2>
          <TopicList chapter={chapter} book={book} read={read} onOpen={onOpen} />
        </>
      ) : (
        <div className="mt-8">
          <Placeholder chapter={chapter} onGenerate={onGenerate} />
        </div>
      )}
    </div>
  );
}

function TopicPage({
  page,
  chapter,
  count,
  prev,
  next,
  book,
  dir,
  images,
  tools,
  onGo,
}: {
  page: LibPage;
  chapter: LibChapter;
  count: number;
  prev: Entry | null;
  next: Entry | null;
  book: LibraryBook;
  dir: "next" | "prev";
  images: ImageHandlers;
  tools: React.ReactNode;
  onGo: (n: number) => void;
}) {
  const label = (e: Entry | null) => (!e ? null : e.kind === "chapter" ? `Chapter: ${book.chapters[e.chapterIdx].title}` : e.page.title);
  const n = book.entries.findIndex((e) => e.kind === "topic" && e.page.chapterId === page.chapterId && e.page.index === page.index) + 1;
  return (
    <div className="book-page max-w-[44rem] pb-24" data-dir={dir}>
      <article data-section={page.index} data-chapter={page.chapterId}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="eyebrow min-w-0 truncate">
            {chapter.title} · Topic {page.number} <span className="opacity-70">of {count}</span> · {page.minutes} min read
          </p>
          <div className="no-print">{tools}</div>
        </div>
        <h1 className="mt-3 font-sans text-[1.9rem] leading-[1.15] font-semibold tracking-[-0.03em] text-balance sm:text-[2.25rem]">{page.title}</h1>
        <div className="my-8" />
        <Markdown images={images}>{page.body}</Markdown>
      </article>

      <nav className="no-print mt-16 grid grid-cols-2 gap-3 border-t border-border pt-8" aria-label="Page navigation">
        <button onClick={() => onGo(prev ? n - 1 : 0)} className="group flex flex-col items-start rounded-lg border border-border px-4 py-3.5 text-left transition-colors hover:border-accent/60">
          <span className="flex items-center gap-1.5 text-[11.5px] font-medium text-faint">
            <ArrowLeft className="size-3.5 transition-transform group-hover:-translate-x-0.5" /> {prev ? "Previous" : "Overview"}
          </span>
          <span className="mt-1 line-clamp-2 text-[14.5px] font-medium tracking-tight text-accent-text">{label(prev) ?? "Back to contents"}</span>
        </button>
        <button onClick={() => onGo(next ? n + 1 : 0)} className="group flex flex-col items-end rounded-lg border border-border px-4 py-3.5 text-right transition-colors hover:border-accent/60">
          <span className="flex items-center gap-1.5 text-[11.5px] font-medium text-faint">
            {next ? "Next" : "Finished"} <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-0.5" />
          </span>
          <span className="mt-1 line-clamp-2 text-[14.5px] font-medium tracking-tight text-accent-text">{label(next) ?? "Back to contents"}</span>
        </button>
      </nav>
      <p className="no-print mt-4 text-center text-[12px] text-faint max-sm:hidden">Use ← → to turn pages</p>
    </div>
  );
}

function SlidesChip({
  spec,
  ids,
  slides,
  slug,
  urls,
  request,
  onExplain,
}: {
  spec: string;
  ids: string[];
  slides: SlideIndex;
  slug: string;
  urls: Record<string, string>;
  request: (paths: string[]) => void;
  onExplain: (sid: string, rect?: ExplainTarget["rect"]) => void;
}) {
  const shown = ids.filter((sid) => slides[sid]);
  if (!shown.length) return null;
  return (
    <Popover.Root onOpenChange={(open) => open && request(shown.map((sid) => `${slug}/${slides[sid].image}`))}>
      <Popover.Trigger asChild>
        <button className="inline-flex h-7 items-center gap-1.5 rounded-full border border-border bg-surface px-2.5 font-sans text-[11.5px] font-medium text-muted shadow-card transition hover:text-text">
          <GalleryHorizontalEnd className="size-3.5" /> Slides {spec.replace(/-/g, "–")}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="end"
          sideOffset={6}
          collisionPadding={12}
          className="rise z-50 max-h-[70dvh] w-[min(560px,calc(100vw-24px))] overflow-y-auto rounded-2xl border border-border bg-surface p-3 shadow-pop"
        >
          <p className="mb-2 px-1 text-[12px] font-medium text-muted">Source slides</p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {shown.map((sid) => {
              const url = urls[`${slug}/${slides[sid].image}`];
              return (
                <figure key={sid} className="group/slide overflow-hidden rounded-xl border border-border bg-surface-2">
                  {url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={url} alt={`Slide ${sid}`} className="aspect-[4/3] w-full bg-white object-contain" loading="lazy" />
                  ) : (
                    <div className="skeleton aspect-[4/3] w-full rounded-none" />
                  )}
                  <figcaption className="flex items-center justify-between gap-2 px-2.5 py-1.5">
                    <span className="truncate text-[12px] text-muted">
                      <span className="font-semibold text-text">{sid}</span> {slides[sid].title}
                    </span>
                    <Popover.Close asChild>
                      <button
                        onClick={(e) => {
                          const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                          onExplain(sid, { x: r.x, y: r.y, w: r.width, h: r.height });
                        }}
                        className="inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1 text-[11.5px] font-medium text-accent-text hover:bg-accent-soft"
                      >
                        <Sparkles className="size-3" /> Explain
                      </button>
                    </Popover.Close>
                  </figcaption>
                </figure>
              );
            })}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

function DocsNav({ book, page, read, notes, onOpen }: { book: LibraryBook; page: number; read: Set<string>; notes: NotesApi["notes"]; onOpen: (n: number) => void }) {
  const currentChapter = page > 0 ? book.entries[page - 1]?.chapterIdx : undefined;
  const [open, setOpen] = useState<Set<number>>(() => new Set(book.chapters.length <= 3 ? book.chapters.map((_, i) => i) : [currentChapter ?? 0]));
  useEffect(() => {
    if (currentChapter === undefined) return;
    setOpen((o) => (o.has(currentChapter) ? o : new Set(o).add(currentChapter)));
  }, [currentChapter]);
  if (!book.entries.length) return <div className="max-lg:hidden" />;
  const item = (active: boolean) =>
    cn(
      "flex w-full items-start gap-2 rounded-md px-2.5 py-1.5 text-left text-[13.5px] leading-snug transition-colors",
      active ? "bg-surface-3 font-medium text-text" : "text-muted hover:bg-surface-2 hover:text-text",
    );
  return (
    <nav aria-label="Chapters and topics" className="no-print sticky top-[112px] max-h-[calc(100dvh-128px)] self-start overflow-y-auto pb-8 max-lg:hidden">
      <button onClick={() => onOpen(0)} className={item(page === 0)}>
        <BookOpen className="mt-0.5 size-3.5 shrink-0" /> Overview
      </button>
      {book.chapters.map((c, ci) => {
        const isOpen = open.has(ci);
        const chapterPage = pageNumber(book, ci);
        return (
          <div key={c.id} className="mt-3">
            <div className="flex items-center">
              <button
                aria-label={isOpen ? "Collapse chapter" : "Expand chapter"}
                aria-expanded={isOpen}
                onClick={() => setOpen((o) => { const n = new Set(o); if (n.has(ci)) n.delete(ci); else n.add(ci); return n; })}
                className="grid size-6 shrink-0 place-items-center rounded text-faint hover:text-text"
              >
                <ChevronRight className={cn("size-3 transition-transform", isOpen && "rotate-90")} />
              </button>
              <button onClick={() => onOpen(chapterPage)} className={cn(item(page === chapterPage), "min-w-0 flex-1 text-[13px] font-semibold")}>
                <span className="line-clamp-2 flex-1">{c.title}</span>
                <ChapterPill chapter={c} />
              </button>
            </div>
            {isOpen && c.pages.length > 0 && (
              <ol className="ml-[17px] space-y-px border-l border-border pl-1.5">
                {c.pages.map((p) => {
                  const n = pageNumber(book, ci, p.index);
                  return (
                    <li key={p.index}>
                      <button onClick={() => onOpen(n)} className={item(page === n)} aria-current={page === n ? "page" : undefined}>
                        <span className="line-clamp-2 flex-1">{p.title}</span>
                        {notes[sectionKey(p.chapterId, p.index)] && <NotebookPen className="mt-0.5 size-3 shrink-0 text-accent" aria-label="Has notes" />}
                        {read.has(readKey(p)) && <Check className="mt-0.5 size-3 shrink-0 text-success" />}
                      </button>
                    </li>
                  );
                })}
              </ol>
            )}
          </div>
        );
      })}
    </nav>
  );
}

/** "On this page": the open topic's headings, read from the rendered DOM, with scroll-spy. */
function OnThisPage({ root, depKey }: { root: React.RefObject<HTMLDivElement | null>; depKey: string }) {
  const [heads, setHeads] = useState<{ id: string; text: string; level: number }[]>([]);
  const [active, setActive] = useState("");
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    const hs = [...el.querySelectorAll<HTMLElement>(".notes h2[id], .notes h3[id]")];
    setHeads(
      hs.map((h) => {
        const c = h.cloneNode(true) as HTMLElement;
        c.querySelectorAll(".katex-mathml").forEach((n) => n.remove());
        return { id: h.id, text: c.textContent?.trim() ?? "", level: h.tagName === "H2" ? 2 : 3 };
      }),
    );
    setActive(hs[0]?.id ?? "");
    const io = new IntersectionObserver(
      (es) => {
        const v = es.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (v) setActive(v.target.id);
      },
      { rootMargin: "-100px 0px -70% 0px" },
    );
    hs.forEach((h) => io.observe(h));
    return () => io.disconnect();
  }, [root, depKey]);
  if (heads.length < 2) return <div className="max-xl:hidden" />;
  return (
    <nav aria-label="On this page" className="no-print sticky top-[112px] max-h-[calc(100dvh-128px)] self-start overflow-y-auto pb-8 max-xl:hidden">
      <p className="eyebrow mb-2">On this page</p>
      <ul className="border-l border-border">
        {heads.map((h) => (
          <li key={h.id}>
            <button
              onClick={() => document.getElementById(h.id)?.scrollIntoView({ behavior: "smooth" })}
              className={cn(
                "-ml-px block w-full border-l py-1 text-left text-[12.5px] leading-snug transition-colors",
                h.level === 3 ? "pl-6" : "pl-3",
                active === h.id ? "border-accent text-text" : "border-transparent text-muted hover:text-text",
              )}
            >
              {h.text}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
