"use client";

import * as Dialog from "@radix-ui/react-dialog";
import * as Popover from "@radix-ui/react-popover";
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Check,
  ChevronRight,
  Download,
  GalleryHorizontalEnd,
  ListTree,
  Printer,
  ScrollText,
  Sparkles,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ExplainPanel, type ExplainTarget } from "@/components/explain-panel";
import { Markdown, type ImageHandlers } from "@/components/markdown";
import { Button } from "@/components/ui/button";
import { parseBook, type BookPage } from "@/lib/book";
import { supabase } from "@/lib/client";
import { cn } from "@/lib/cn";
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

/** Signed URLs for this notebook's generated files (`assets/x.png`, `slides/A-004.webp`), cached. */
export function useSignedUrls(notebookId: string) {
  const [urls, setUrls] = useState<Record<string, string>>({});
  const pending = useRef(new Set<string>());
  const request = useCallback(
    async (paths: string[]) => {
      const todo = paths.filter((p) => !pending.current.has(p));
      if (!todo.length) return;
      todo.forEach((p) => pending.current.add(p));
      const { data } = await supabase().storage.from("outputs").createSignedUrls(todo.map((p) => `${notebookId}/${p}`), 3600);
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

export function Reader({
  notebookId,
  level,
  md,
  slides,
}: {
  notebookId: string;
  level: string;
  md: string;
  slides: SlideIndex | null;
}) {
  const book = useMemo(() => parseBook(md), [md]);
  const total = book.pages.length;
  const hasPages = total > 0;
  const { urls, request } = useSignedUrls(notebookId);

  // Mounted only after the notes were fetched in the browser, so reading localStorage here is safe.
  const [mode, setMode] = useState<Mode>(() => stored(`mode:${notebookId}`, "book", (r) => (r === "scroll" ? "scroll" : "book")));
  const [page, setPage] = useState<number>(() => stored(`page:${notebookId}`, 0, (r) => Math.min(Math.max(0, Number(r) || 0), total)));
  const [read, setRead] = useState<Set<number>>(() => stored(`read:${notebookId}`, new Set<number>(), (r) => new Set<number>(JSON.parse(r))));
  const [dir, setDir] = useState<"next" | "prev">("next");
  const [printing, setPrinting] = useState(false);
  const [zoom, setZoom] = useState<{ url: string; alt: string } | null>(null);
  const [target, setTarget] = useState<ExplainTarget | null>(null);
  const [pick, setPick] = useState<ExplainTarget | null>(null); // floating "Explain" button for a text selection
  const container = useRef<HTMLDivElement>(null);

  const showAll = mode === "scroll" || printing || !hasPages;

  const go = useCallback(
    (n: number) => {
      const next = Math.min(Math.max(0, n), total);
      setDir(next >= page ? "next" : "prev");
      setPage(next);
      setTarget(null);
      setPick(null);
      window.scrollTo({ top: 0 });
    },
    [page, total],
  );

  useEffect(() => {
    save(`page:${notebookId}`, String(page));
    if (page > 0) {
      setRead((prev) => {
        if (prev.has(page)) return prev;
        const next = new Set(prev).add(page);
        save(`read:${notebookId}`, JSON.stringify([...next]));
        return next;
      });
    }
  }, [page, notebookId]);

  const setViewMode = (m: Mode) => {
    setMode(m);
    save(`mode:${notebookId}`, m);
    setTarget(null);
    setPick(null);
    window.scrollTo({ top: 0 });
  };

  // Images: only this page and the next one in book mode; everything when scrolling or printing.
  useEffect(() => {
    if (showAll) return void request(assetPaths(md));
    const cur = page > 0 ? book.pages[page - 1] : null;
    request([...assetPaths(cur ? cur.body : book.intro), ...assetPaths(book.pages[page]?.body ?? "")]);
  }, [showAll, md, page, book, request]);

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
    (section: number): ImageHandlers => ({
      resolve: (src) => urls[src],
      onZoom: (url, alt) => setZoom({ url, alt }),
      onExplain: (src) => {
        const el = container.current?.querySelector(`img[src="${CSS.escape(urls[src] ?? "")}"]`);
        const r = el?.getBoundingClientRect();
        setTarget({ section, image: src, label: "This figure", rect: r && { x: r.x, y: r.y, w: r.width, h: Math.min(r.height, 80) } });
      },
    }),
    [urls],
  );
  const plainImages = useMemo<ImageHandlers>(() => ({ resolve: (s) => urls[s], onZoom: (url, alt) => setZoom({ url, alt }) }), [urls]);

  // text selection -> floating Explain button
  useEffect(() => {
    const onUp = () => {
      setTimeout(() => {
        const sel = window.getSelection();
        const text = sel?.toString().trim() ?? "";
        if (!sel || sel.rangeCount === 0 || text.length < 3 || text.length > 1500) return setPick(null);
        const node = sel.anchorNode instanceof Element ? sel.anchorNode : sel.anchorNode?.parentElement;
        const sec = node?.closest<HTMLElement>("[data-section]");
        if (!sec || !container.current?.contains(sec)) return setPick(null);
        const r = sel.getRangeAt(0).getBoundingClientRect();
        setPick({ section: Number(sec.dataset.section), selection: text, rect: { x: r.x, y: r.y, w: r.width, h: r.height } });
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
    const blob = new Blob([md], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${book.title.replace(/[^\w -]+/g, "").trim() || "notes"} - ${LEVEL_NAMES[level] ?? level}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const explainSlide = (section: number, sid: string, rect?: ExplainTarget["rect"]) =>
    setTarget({ section, image: `slide:${sid}`, label: `Slide ${sid}: ${slides?.[sid]?.title ?? ""}`, rect });
  const chip = (p: BookPage) =>
    slides && p.slides.length > 0 ? (
      <SlidesChip spec={p.spec} ids={p.slides} slides={slides} urls={urls} request={request} onExplain={(sid, rect) => explainSlide(p.index, sid, rect)} />
    ) : null;

  const levelName = LEVEL_NAMES[level] ?? level;
  const progress = hasPages ? (showAll ? 0 : page / total) : 0;

  return (
    <div ref={container} className="relative">
      <div className="no-print sticky top-14 z-20 -mx-4 mb-6 flex items-center gap-2 border-b border-border bg-bg/85 px-4 py-2.5 backdrop-blur sm:-mx-8 sm:px-8">
        <span className="text-[13px] font-semibold whitespace-nowrap">{levelName}</span>
        <span className="truncate text-[12.5px] text-faint max-sm:hidden">
          {hasPages && !showAll ? (page === 0 ? "· Cover" : `· Topic ${page} of ${total}`) : `· ${book.minutes} min read`}
        </span>
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
                  {book.pages.map((p) => (
                    <Popover.Close asChild key={p.index}>
                      <button
                        onClick={() => {
                          if (mode === "book") go(p.number);
                          else document.getElementById(`topic-${p.number}`)?.scrollIntoView({ behavior: "smooth" });
                        }}
                        className={cn("flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] hover:bg-surface-2", !showAll && page === p.number && "bg-accent-soft text-accent-text")}
                      >
                        <span className={cn("grid size-5 shrink-0 place-items-center rounded-md text-[11px] font-semibold tabular-nums", read.has(p.number) ? "bg-success-soft text-success" : "bg-surface-2 text-muted")}>
                          {read.has(p.number) ? <Check className="size-3" /> : p.number}
                        </span>
                        <span className="min-w-0 flex-1 truncate">{p.title}</span>
                      </button>
                    </Popover.Close>
                  ))}
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
            <div className="h-full bg-accent transition-all duration-300" style={{ width: `${progress * 100}%` }} />
          </div>
        )}
      </div>

      {showAll ? (
        <article className="mx-auto max-w-[44rem] pb-32">
          <Markdown images={plainImages}>{[`# ${book.title}`, book.intro].filter(Boolean).join("\n\n")}</Markdown>
          {book.pages.map((p) => (
            <section key={p.index} id={`topic-${p.number}`} data-section={p.index} className={cn("scroll-mt-28", p.number > 1 && "print:break-before-page")}>
              <div className="no-print flex justify-end pt-8 pb-1">{chip(p)}</div>
              <Markdown images={imageHandlers(p.index)}>{`## ${p.title}\n\n${p.body}`}</Markdown>
            </section>
          ))}
        </article>
      ) : page === 0 ? (
        <Cover book={book} levelName={levelName} read={read} dir={dir} images={plainImages} onOpen={go} />
      ) : (
        <TopicPage
          key={page}
          page={book.pages[page - 1]}
          total={total}
          prev={page > 1 ? book.pages[page - 2] : null}
          next={page < total ? book.pages[page] : null}
          dir={dir}
          images={imageHandlers(book.pages[page - 1].index)}
          chip={chip(book.pages[page - 1])}
          onGo={go}
        />
      )}

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

function Cover({
  book,
  levelName,
  read,
  dir,
  images,
  onOpen,
}: {
  book: ReturnType<typeof parseBook>;
  levelName: string;
  read: Set<number>;
  dir: "next" | "prev";
  images: ImageHandlers;
  onOpen: (n: number) => void;
}) {
  const meta = [
    `${book.pages.length} topic${book.pages.length === 1 ? "" : "s"}`,
    `${book.slideCount} slide${book.slideCount === 1 ? "" : "s"}`,
    book.decks.length > 1 ? `${book.decks.length} PDFs` : null,
    `${book.minutes} min read`,
  ].filter(Boolean);
  const firstUnread = book.pages.find((p) => !read.has(p.number))?.number ?? 1;
  const started = read.size > 0;
  return (
    <div className={cn("mx-auto max-w-[46rem] pb-24", dir === "next" ? "turn-next" : "turn-prev")}>
      <div className="overflow-hidden rounded-3xl border border-border bg-surface shadow-card max-sm:rounded-2xl">
        <div className="h-1.5 bg-gradient-to-r from-accent via-accent/60 to-accent/10" />
        <div className="px-6 py-9 sm:px-12 sm:py-12">
          <p className="text-[11.5px] font-semibold tracking-[0.14em] text-accent-text uppercase">{levelName} · Study book</p>
          <h1 className="mt-3 font-sans text-[2.15rem] leading-[1.1] font-semibold tracking-[-0.03em] text-balance sm:text-[2.6rem]">{book.title}</h1>
          <p className="mt-4 text-[13.5px] text-muted">{meta.join(" · ")}</p>
          {book.decks.length > 0 && (
            <p className="mt-1 line-clamp-2 text-[12.5px] text-faint">From {book.decks.join(" · ")}</p>
          )}
          {book.intro && <Markdown images={images} className="mt-8">{book.intro}</Markdown>}
          <Button variant="primary" className="mt-8" onClick={() => onOpen(started ? firstUnread : 1)}>
            {started ? "Continue reading" : "Start reading"} <ArrowRight className="size-4" />
          </Button>
        </div>
      </div>

      <h2 className="mt-10 mb-3 text-[11.5px] font-semibold tracking-[0.14em] text-faint uppercase">Contents</h2>
      <ol className="overflow-hidden rounded-2xl border border-border bg-surface shadow-card">
        {book.pages.map((p) => (
          <li key={p.index} className="border-b border-border last:border-0">
            <button onClick={() => onOpen(p.number)} className="group flex w-full items-center gap-3.5 px-4 py-3.5 text-left transition-colors hover:bg-surface-2 sm:px-5">
              <span className={cn("grid size-7 shrink-0 place-items-center rounded-lg text-[12px] font-semibold tabular-nums", read.has(p.number) ? "bg-success-soft text-success" : "bg-surface-2 text-muted group-hover:bg-accent-soft group-hover:text-accent-text")}>
                {read.has(p.number) ? <Check className="size-3.5" /> : p.number}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[14.5px] font-medium tracking-tight">{p.title}</span>
                <span className="block text-[12px] text-faint">
                  Slides {p.spec.replace(/-/g, "–")} · {p.minutes} min
                </span>
              </span>
              <ChevronRight className="size-4 shrink-0 text-faint transition-transform group-hover:translate-x-0.5 group-hover:text-text" />
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}

function TopicPage({
  page,
  total,
  prev,
  next,
  dir,
  images,
  chip,
  onGo,
}: {
  page: BookPage;
  total: number;
  prev: BookPage | null;
  next: BookPage | null;
  dir: "next" | "prev";
  images: ImageHandlers;
  chip: React.ReactNode;
  onGo: (n: number) => void;
}) {
  return (
    <div className={cn("book-page mx-auto max-w-[46rem] pb-24", dir === "next" ? "turn-next" : "turn-prev")}>
      <article
        data-section={page.index}
        className="rounded-3xl border border-border bg-surface px-6 py-8 shadow-card sm:px-12 sm:py-12 max-sm:rounded-none max-sm:border-0 max-sm:bg-transparent max-sm:px-0 max-sm:py-2 max-sm:shadow-none"
      >
        <div className="flex items-center justify-between gap-3">
          <p className="text-[11.5px] font-semibold tracking-[0.14em] text-accent-text uppercase">
            Topic {page.number} <span className="text-faint">of {total}</span>
          </p>
          <div className="no-print">{chip}</div>
        </div>
        <h1 className="mt-3 font-sans text-[1.85rem] leading-[1.15] font-semibold tracking-[-0.025em] text-balance sm:text-[2.2rem]">{page.title}</h1>
        <p className="mt-2 text-[12.5px] text-faint">{page.minutes} min read</p>
        <hr className="my-7 border-border" />
        <Markdown images={images}>{page.body}</Markdown>
      </article>

      <nav className="no-print mt-5 grid grid-cols-2 gap-3" aria-label="Page navigation">
        <button
          onClick={() => onGo(prev ? prev.number : 0)}
          className="group flex flex-col items-start rounded-2xl border border-border bg-surface px-4 py-3.5 text-left shadow-card transition-all hover:-translate-y-px hover:border-border-strong"
        >
          <span className="flex items-center gap-1.5 text-[11.5px] font-medium text-faint">
            <ArrowLeft className="size-3.5 transition-transform group-hover:-translate-x-0.5" /> {prev ? "Previous" : "Cover"}
          </span>
          <span className="mt-1 line-clamp-2 text-[14px] font-medium tracking-tight">{prev ? prev.title : "Back to contents"}</span>
        </button>
        <button
          onClick={() => onGo(next ? next.number : 0)}
          className="group flex flex-col items-end rounded-2xl border border-border bg-surface px-4 py-3.5 text-right shadow-card transition-all hover:-translate-y-px hover:border-border-strong"
        >
          <span className="flex items-center gap-1.5 text-[11.5px] font-medium text-faint">
            {next ? "Next" : "Finished"} <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-0.5" />
          </span>
          <span className="mt-1 line-clamp-2 text-[14px] font-medium tracking-tight">{next ? next.title : "Back to contents"}</span>
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
  urls,
  request,
  onExplain,
}: {
  spec: string;
  ids: string[];
  slides: SlideIndex;
  urls: Record<string, string>;
  request: (paths: string[]) => void;
  onExplain: (sid: string, rect?: ExplainTarget["rect"]) => void;
}) {
  const shown = ids.filter((sid) => slides[sid]);
  if (!shown.length) return null;
  return (
    <Popover.Root onOpenChange={(open) => open && request(shown.map((sid) => slides[sid].image))}>
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
              const url = urls[slides[sid].image];
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
