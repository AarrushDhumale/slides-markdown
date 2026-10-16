"use client";

import * as Dialog from "@radix-ui/react-dialog";
import * as Popover from "@radix-ui/react-popover";
import { Download, GalleryHorizontalEnd, ListTree, Printer, Sparkles, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ExplainPanel, type ExplainTarget } from "@/components/explain-panel";
import { Markdown, type ImageHandlers } from "@/components/markdown";
import { Button } from "@/components/ui/button";
import { supabase } from "@/lib/client";
import { cn } from "@/lib/cn";
import { assetPaths, splitDoc, stripContents, toc } from "@/lib/slide-anchors";

export type SlideIndex = Record<string, { image: string; title: string }>;

const LEVEL_NAMES: Record<string, string> = {
  summary: "Summary",
  learner: "Learner notes",
  proficient: "Revision sheet",
  dockey: "Explain simply",
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
  const { preamble, sections } = useMemo(() => splitDoc(md), [md]);
  const contents = useMemo(() => toc(md).filter((t, _, all) => (all.some((x) => x.level === 2) ? t.level === 2 : true)), [md]);
  const { urls, request } = useSignedUrls(notebookId);
  const [zoom, setZoom] = useState<{ url: string; alt: string } | null>(null);
  const [target, setTarget] = useState<ExplainTarget | null>(null);
  const [pick, setPick] = useState<ExplainTarget | null>(null); // floating "Explain" button for a text selection
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    request(assetPaths(md));
  }, [md, request]);

  const words = useMemo(() => md.split(/\s+/).length, [md]);

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
  const handlers = useMemo(() => sections.map((s) => imageHandlers(s.index)), [sections, imageHandlers]);

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
    a.download = `${(md.match(/^# (.+)$/m)?.[1] ?? "notes").replace(/[^\w -]+/g, "").trim()} - ${LEVEL_NAMES[level] ?? level}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div ref={container} className="relative">
      <div className="no-print sticky top-14 z-20 -mx-4 mb-6 flex items-center gap-2 border-b border-border bg-bg/85 px-4 py-2.5 backdrop-blur sm:-mx-8 sm:px-8">
        <span className="text-[13px] font-semibold">{LEVEL_NAMES[level] ?? level}</span>
        <span className="text-[12.5px] text-faint">· {Math.max(1, Math.round(words / 230))} min read</span>
        <div className="ml-auto flex items-center gap-1">
          {contents.length > 1 && (
            <Popover.Root>
              <Popover.Trigger asChild>
                <Button size="sm" variant="ghost">
                  <ListTree className="size-4" /> <span className="max-sm:hidden">Contents</span>
                </Button>
              </Popover.Trigger>
              <Popover.Portal>
                <Popover.Content align="end" sideOffset={6} className="rise z-50 max-h-[60dvh] w-[320px] overflow-y-auto rounded-xl border border-border bg-surface p-1.5 shadow-pop">
                  {contents.map((t) => (
                    <Popover.Close asChild key={t.id + t.title}>
                      <button
                        onClick={() => document.getElementById(t.id)?.scrollIntoView({ behavior: "smooth" })}
                        className={cn("block w-full rounded-lg px-2.5 py-1.5 text-left text-[13px] hover:bg-surface-2", t.level === 3 && "pl-5 text-muted")}
                      >
                        {t.title}
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
          <Button size="sm" variant="ghost" onClick={() => window.print()} title="Print or save as PDF">
            <Printer className="size-4" />
          </Button>
        </div>
      </div>

      <article className="mx-auto max-w-[44rem] pb-32">
        <Markdown images={{ resolve: (s) => urls[s], onZoom: (url, alt) => setZoom({ url, alt }) }}>{stripContents(preamble)}</Markdown>
        {sections.map((s) => (
          <section key={s.index} data-section={s.index} className="group/sec relative">
            {slides && s.slides.length > 0 && (
              <SlidesChip
                spec={s.spec}
                ids={s.slides}
                slides={slides}
                urls={urls}
                request={request}
                onExplain={(sid, rect) => setTarget({ section: s.index, image: `slide:${sid}`, label: `Slide ${sid}: ${slides[sid]?.title ?? ""}`, rect })}
              />
            )}
            <Markdown images={handlers[s.index]}>{s.md}</Markdown>
          </section>
        ))}
      </article>

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
      <div className="no-print flex justify-end pt-8 pb-1">
        <Popover.Trigger asChild>
          <button className="inline-flex h-7 items-center gap-1.5 rounded-full border border-border bg-surface px-2.5 font-sans text-[11.5px] font-medium text-muted opacity-70 shadow-card transition hover:text-text hover:opacity-100 group-hover/sec:opacity-100 max-sm:opacity-100">
            <GalleryHorizontalEnd className="size-3.5" /> Slides {spec.replace(/-/g, "–")}
          </button>
        </Popover.Trigger>
      </div>
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
