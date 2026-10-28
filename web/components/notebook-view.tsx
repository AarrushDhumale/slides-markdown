"use client";

import { ArrowLeft, BookOpenText, Cpu, PanelLeft } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { pdfsFrom } from "@/components/dropzone";
import { MyNotes } from "@/components/my-notes";
import { useNotes } from "@/components/notes-panel";
import { Quiz, type QuizChapter } from "@/components/quiz";
import { Reader, type SlideIndex } from "@/components/reader";
import { StatusBadge } from "@/components/status";
import { ACTIONS, docLevel, ProgressCard, Sources, Studio, workerOnline, type LevelStatus, type View } from "@/components/studio";
import { ThemeToggle } from "@/components/theme-toggle";
import { api, ensureSession, supabase, uploadPdfs, type Chapter, type ChapterDoc, type Job, type Notebook, type Source, type WorkerStatus } from "@/lib/client";
import { parseLibraryBook, type Variant } from "@/lib/book";
import { cn } from "@/lib/cn";
import { demoDir } from "@/lib/demo";
import { sectionKey } from "@/lib/library";
import type { RewriteLevel } from "@/lib/limits";

async function fetchText(path: string): Promise<string> {
  if (path.startsWith("demo")) {
    const res = await fetch(`/${path}`);
    if (!res.ok) throw new Error("Demo file missing");
    return res.text();
  }
  const { data, error } = await supabase().storage.from("library").download(path);
  if (error || !data) throw new Error(error?.message ?? "Could not load the notes");
  return data.text();
}

const DEMO = "demo";
const demoNow = new Date().toISOString();
const DEMO_NOTEBOOK: Notebook = { id: DEMO, title: "Theory of Computation (demo)", status: "ready", slide_count: 120, created_at: demoNow, updated_at: demoNow };
const DEMO_CHAPTER: Chapter = { id: DEMO, slug: DEMO, title: "Theory of Computation", status: "ready", error: null };
const DEMO_DOCS: ChapterDoc[] = [
  { chapter_id: DEMO, level: "summary", storage_path: "summary.md", word_count: 2400, updated_at: demoNow },
  { chapter_id: DEMO, level: "learner", storage_path: "learner.md", word_count: 9800, updated_at: demoNow },
  { chapter_id: DEMO, level: "quiz", storage_path: "quiz.json", word_count: 0, updated_at: demoNow },
];
const DEMO_SOURCES: Source[] = ["Non-regular languages.pdf", "FSA minimization.pdf", "Context-free grammars.pdf", "Ambiguity in CFGs.pdf"].map((filename, i) => ({
  id: `d${i}`, filename, size_bytes: 2_400_000 + i * 310_000, pages: 28 + i * 3, position: i, chapter_id: DEMO,
}));

export function NotebookView({ id }: { id: string }) {
  const demo = id === DEMO;
  const [notebook, setNotebook] = useState<Notebook | null>(null);
  const [missing, setMissing] = useState(false);
  const [sources, setSources] = useState<Source[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [chapters, setChapters] = useState<Chapter[]>([]); // one per distinct PDF, in source order
  const [docs, setDocs] = useState<ChapterDoc[]>([]);
  const [variants, setVariants] = useState<Record<string, Variant>>({}); // this notebook's per-section level choices
  const [worker, setWorker] = useState<WorkerStatus | null>(null);
  const [view, setView] = useState<View>("summary");
  const [content, setContent] = useState<Record<string, string>>({}); // `${chapter id}:${level}@${updated_at}` -> text
  const [slides, setSlides] = useState<Record<string, SlideIndex | null>>({}); // by chapter slug
  const [rewriting, setRewriting] = useState<string | null>(null);
  const [mobilePane, setMobilePane] = useState<"notes" | "tools">("notes");
  const [sideOpen, setSideOpen] = useState(true); // desktop: collapse the sources/tools column to give the reader room
  const [uploading, setUploading] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const userPicked = useRef(false);
  const shown = useRef(false); // the reader has been on screen once (later arrivals must not blank it)
  const notes = useNotes(id, demo);

  const load = useCallback(async () => {
    if (demo) {
      const set = new URLSearchParams(location.search).get("set");
      const nb = set ? { ...DEMO_NOTEBOOK, title: `Neural Networks (${set})` } : DEMO_NOTEBOOK;
      setNotebook(nb);
      setTitle(nb.title);
      setSources(DEMO_SOURCES);
      setChapters([DEMO_CHAPTER]);
      setDocs(DEMO_DOCS);
      return;
    }
    await ensureSession();
    const db = supabase();
    const [nb, src, jb, wk, ch] = await Promise.all([
      db.from("notebooks").select("*").eq("id", id).maybeSingle(),
      db.from("sources").select("*").eq("notebook_id", id).order("position"),
      db.from("jobs").select("*").eq("notebook_id", id).order("created_at", { ascending: false }).limit(10),
      db.from("worker_status").select("*").order("last_seen", { ascending: false }).limit(1),
      db.from("section_choices").select("chapter_id, section, level").eq("notebook_id", id),
    ]);
    if (!nb.data) {
      setMissing(true);
      return;
    }
    const sourceRows = (src.data ?? []) as Source[];
    const ids = [...new Set(sourceRows.map((s) => s.chapter_id).filter((c): c is string => !!c))];
    const [chRows, docRows] = ids.length
      ? await Promise.all([db.from("chapters").select("id, slug, title, status, error").in("id", ids), db.from("chapter_docs").select("*").in("chapter_id", ids)])
      : [{ data: [] }, { data: [] }];
    const byId = new Map(((chRows.data ?? []) as Chapter[]).map((c) => [c.id, c]));

    // the rewrites this notebook chose (shared rows, so a section someone else already rewrote loads instantly)
    const choices = (ch.data ?? []) as { chapter_id: string; section: number; level: string }[];
    const next: Record<string, Variant> = {};
    for (const chapterId of new Set(choices.map((c) => c.chapter_id))) {
      const mine = choices.filter((c) => c.chapter_id === chapterId);
      const { data } = await db.from("section_variants").select("section, level, body").eq("chapter_id", chapterId).in("section", mine.map((c) => c.section));
      for (const c of mine) {
        const v = (data ?? []).find((r) => r.section === c.section && r.level === c.level);
        if (v) next[sectionKey(chapterId, c.section)] = { level: v.level, body: v.body };
      }
    }
    setNotebook(nb.data as Notebook);
    setTitle((t) => t || (nb.data as Notebook).title);
    setSources(sourceRows);
    setJobs((jb.data ?? []) as Job[]);
    setChapters(ids.flatMap((c) => (byId.has(c) ? [byId.get(c)!] : [])));
    setDocs((docRows.data ?? []) as ChapterDoc[]);
    setVariants(next);
    setWorker(((wk.data ?? [])[0] as WorkerStatus) ?? null);
  }, [id, demo]);

  useEffect(() => {
    if (demo) {
      load();
      return;
    }
    load().catch((e) => toast.error(e instanceof Error ? e.message : "Could not load this notebook"));
    const db = supabase();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const soon = () => {
      clearTimeout(timer); // a burst of changes (a chapter publishing many docs) is one reload
      timer = setTimeout(() => load().catch(() => {}), 250);
    };
    const channel = db
      .channel(`notebook-${id}`)
      .on("postgres_changes", { event: "*", schema: "public", table: "jobs", filter: `notebook_id=eq.${id}` }, soon)
      .on("postgres_changes", { event: "*", schema: "public", table: "chapter_docs" }, soon)
      .on("postgres_changes", { event: "*", schema: "public", table: "chapters" }, soon)
      .on("postgres_changes", { event: "*", schema: "public", table: "section_choices", filter: `notebook_id=eq.${id}` }, soon)
      .on("postgres_changes", { event: "UPDATE", schema: "public", table: "notebooks", filter: `id=eq.${id}` }, soon)
      .on("postgres_changes", { event: "*", schema: "public", table: "worker_status" }, (p) => setWorker(p.new as WorkerStatus))
      .subscribe();
    // fallback refresh while something is in flight (Realtime may be blocked on some networks)
    const poll = setInterval(() => load().catch(() => {}), 15_000);
    return () => {
      clearTimeout(timer);
      db.removeChannel(channel);
      clearInterval(poll);
    };
  }, [id, load, demo]);

  // how many chapters have each level
  const status = useMemo<LevelStatus>(() => {
    const out: LevelStatus = {};
    for (const level of ["summary", "notes", "learner", "proficient", "donkey", "quiz"]) {
      out[level] = { ready: chapters.filter((c) => docs.some((d) => d.chapter_id === c.id && d.level === level)).length, total: chapters.length };
    }
    return out;
  }, [chapters, docs]);

  // open the first view that has something (summary first) unless the user picked one
  useEffect(() => {
    if (userPicked.current) return;
    if (demo) {
      const v = new URLSearchParams(location.search).get("view") as View | null;
      setView(v ?? "summary");
      return;
    }
    const first = ACTIONS.find((a) => a.key !== "mynotes" && (status[docLevel(a.key, a.level)]?.ready ?? 0) > 0);
    if (first) setView(first.key);
  }, [status, demo]);

  // "My notes" lists topics, so it reads whichever level exists (learner first)
  const level = view === "quiz" ? "quiz" : view === "mynotes" ? (["learner", "summary", "notes", "proficient", "donkey"].find((l) => (status[l]?.ready ?? 0) > 0) ?? "learner") : view;
  const levelDocs = docs.filter((d) => d.level === level);
  const docKey = (d: ChapterDoc) => `${d.chapter_id}:${level}@${d.updated_at}`;

  useEffect(() => {
    for (const d of levelDocs) {
      const key = docKey(d);
      if (content[key] !== undefined) continue;
      fetchText(demo ? `${demoDir()}/${d.storage_path}` : d.storage_path)
        .then((text) => setContent((c) => ({ ...c, [key]: text })))
        .catch((e) => toast.error(e.message));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [levelDocs.map((d) => d.chapter_id + d.updated_at).join(), level, demo]);

  // the slide index of every chapter that has a learner doc (re-read when it is regenerated)
  const slideStamps = docs.filter((d) => d.level === "learner").map((d) => `${d.chapter_id}@${d.updated_at}`).join();
  useEffect(() => {
    if (demo) return;
    for (const c of chapters) {
      if (!docs.some((d) => d.chapter_id === c.id && d.level === "learner")) continue;
      fetchText(`${c.slug}/slides.json`)
        .then((t) => setSlides((s) => ({ ...s, [c.slug]: JSON.parse(t) })))
        .catch(() => setSlides((s) => ({ ...s, [c.slug]: null })));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slideStamps, demo]);

  const pending = levelDocs.some((d) => content[docKey(d)] === undefined);
  const book = useMemo(
    () =>
      parseLibraryBook(
        chapters.map((c) => {
          const d = levelDocs.find((x) => x.chapter_id === c.id);
          const md = d ? (content[docKey(d)] ?? null) : null;
          // a doc that is still downloading reads as "being written" for a moment rather than "missing"
          return { id: c.id, slug: c.slug, title: c.title, status: d && md === null ? "generating" : c.status, error: c.error, md };
        }),
        variants,
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [chapters, docs, content, variants, level],
  );

  const generate = async (lvl: string) => {
    try {
      const res = await api<{ ready?: boolean }>("/api/jobs", { notebook_id: id, levels: lvl === "summary" || lvl === "notes" ? [lvl, "learner"] : [lvl] });
      toast.success(res.ready ? "Already generated. Nothing to wait for." : "Queued. Chapters appear here as they are written.");
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not start");
    }
  };

  const addPdfs = async (list: FileList) => {
    const { ok, rejected } = pdfsFrom(list);
    rejected.forEach((r) => toast.error(r));
    if (!ok.length) return;
    try {
      await uploadPdfs(id, ok, (name) => setUploading(`Uploading ${name}…`));
      setUploading("Starting…");
      // the levels this notebook already has; chapters made before (by anyone) are reused, only new ones are written
      const have = Object.entries(status).filter(([l, s]) => s.ready > 0 && l !== "quiz").map(([l]) => l);
      const res = await api<{ ready?: boolean }>("/api/jobs", { notebook_id: id, levels: [...new Set([...have, "summary", "learner"])] });
      toast.success(res.ready ? "Added. These slides were already converted, so they are ready now." : "Added. Writing the new chapters…");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(null);
      load();
    }
  };

  const choose = async (chapterId: string, section: number, lvl: RewriteLevel | null) => {
    const key = sectionKey(chapterId, section);
    setRewriting(key);
    try {
      const res = await api<{ level: RewriteLevel | null; body?: string; cached?: boolean }>("/api/sections/rewrite", { notebook_id: id, chapter_id: chapterId, section, level: lvl });
      setVariants((v) => {
        const next = { ...v };
        if (res.level && res.body) next[key] = { level: res.level, body: res.body };
        else delete next[key];
        return next;
      });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not rewrite this topic");
    } finally {
      setRewriting(null);
    }
  };

  const openTopic = (chapterIdx: number, section: number) => {
    const chapter = book.chapters[chapterIdx];
    try {
      localStorage.setItem(`page:${id}`, `t:${chapter.slug}:${section}`);
    } catch {
      /* the reader just opens at its last place */
    }
    userPicked.current = true;
    setView(level as View);
  };

  const rename = async () => {
    const clean = title.trim().slice(0, 120);
    if (!notebook || !clean || clean === notebook.title) return setTitle(notebook?.title ?? "");
    const { error } = await supabase().from("notebooks").update({ title: clean }).eq("id", id);
    if (error) toast.error(error.message);
    else setNotebook({ ...notebook, title: clean });
  };

  if (missing) {
    return (
      <div className="grid min-h-dvh place-items-center px-4 text-center">
        <div>
          <p className="text-lg font-semibold">Notebook not found</p>
          <p className="mt-1 text-sm text-muted">It may have been made in another browser. Notebooks stay with the browser that created them.</p>
          <Link href="/" className="mt-4 inline-block text-sm font-medium text-accent-text underline underline-offset-4">
            Back to your notebooks
          </Link>
        </div>
      </div>
    );
  }

  const busyJob = jobs.some((j) => j.status === "queued" || j.status === "running");
  const online = workerOnline(worker);
  const showReader = !!notebook && chapters.length > 0 && view !== "quiz" && view !== "mynotes" && (shown.current || !pending);
  if (showReader) shown.current = true;
  const quiz: QuizChapter[] = chapters.flatMap((c) => {
    const d = docs.find((x) => x.chapter_id === c.id && x.level === "quiz");
    const text = d ? content[`${c.id}:quiz@${d.updated_at}`] : undefined;
    return safeQuiz(text ?? "").map((q) => ({ ...q, chapter: chapters.length > 1 ? `${c.title}: ${q.chapter}` : q.chapter }));
  });

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="no-print sticky top-0 z-30 flex h-14 shrink-0 items-center gap-2 border-b border-border bg-bg/85 px-3 backdrop-blur sm:px-5">
        <Link href="/" className="grid size-9 place-items-center rounded-lg text-muted hover:bg-surface-2 hover:text-text" aria-label="All notebooks">
          <ArrowLeft className="size-[18px]" />
        </Link>
        {notebook ? (
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={rename}
            onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
            aria-label="Notebook title"
            className="min-w-0 flex-1 truncate rounded-lg bg-transparent px-2 py-1 text-[15px] font-semibold tracking-tight outline-none hover:bg-surface-2 focus:bg-surface-2"
          />
        ) : (
          <div className="skeleton h-5 w-48" />
        )}
        {notebook && <span className="max-sm:hidden"><StatusBadge status={notebook.status} /></span>}
        <button
          className="grid size-9 place-items-center rounded-lg text-muted hover:bg-surface-2"
          onClick={() => (window.innerWidth >= 1024 ? setSideOpen((o) => !o) : setMobilePane((p) => (p === "notes" ? "tools" : "notes")))}
          aria-label="Toggle sources and tools"
        >
          <PanelLeft className="size-[18px]" />
        </button>
        <ThemeToggle />
      </header>

      <div className="flex w-full flex-1">
        <aside
          className={cn(
            "no-print w-full shrink-0 border-border bg-bg px-4 py-5 lg:sticky lg:top-14 lg:h-[calc(100dvh-56px)] lg:w-[280px] lg:overflow-y-auto lg:border-r",
            sideOpen ? "lg:block" : "lg:hidden",
            mobilePane === "tools" ? "block" : "hidden",
          )}
        >
          <section>
            <h2 className="mb-2 px-1.5 text-[11.5px] font-semibold tracking-wider text-faint uppercase">Sources</h2>
            <Sources sources={sources} onAdd={addPdfs} busy={uploading} />
          </section>
          <section className="mt-6">
            <h2 className="mb-2 px-1.5 text-[11.5px] font-semibold tracking-wider text-faint uppercase">Study tools</h2>
            <Studio
              status={status}
              jobs={jobs}
              view={view}
              noteCount={Object.keys(notes.notes).length}
              onSelect={(v) => {
                userPicked.current = true;
                setView(v);
                setMobilePane("notes");
              }}
              onGenerate={generate}
            />
          </section>
          <section className="mt-4 space-y-3">
            <ProgressCard jobs={jobs} worker={worker} onRetry={(j) => api("/api/jobs", { notebook_id: id, levels: j.levels }).then(load).catch((e) => toast.error(e.message))} />
          </section>
          <p className="mt-6 flex items-center gap-1.5 px-1.5 text-[11.5px] text-faint">
            <Cpu className="size-3" />
            {online ? (worker?.local_model ? "Generator online · local model ready" : "Generator online") : "Generator offline"}
          </p>
        </aside>

        <main className={cn("min-w-0 flex-1 px-4 sm:px-6", mobilePane === "tools" ? "max-lg:hidden" : "")}>
          {!notebook ? (
            <ReaderSkeleton />
          ) : chapters.length === 0 ? (
            <EmptyState busy={busyJob} hasSources={sources.length > 0} />
          ) : view === "mynotes" ? (
            pending && !shown.current ? <ReaderSkeleton /> : <MyNotes book={book} notes={notes.notes} title={title || notebook.title} onOpen={openTopic} />
          ) : view === "quiz" ? (
            status.quiz.ready === 0 ? (
              <EmptyState busy={busyJob} hasSources />
            ) : (
              <div className="pt-8">
                <Quiz notebookId={id} quiz={quiz} />
              </div>
            )
          ) : showReader ? (
            <Reader
              key={level}
              notebookId={id}
              notebookTitle={title || notebook.title}
              level={level}
              book={book}
              slides={slides}
              rewriting={rewriting}
              notes={notes}
              onChoose={choose}
              onGenerate={() => generate(level)}
            />
          ) : (
            <ReaderSkeleton />
          )}
        </main>
      </div>
    </div>
  );
}

function safeQuiz(text: string): QuizChapter[] {
  try {
    const q = JSON.parse(text);
    return Array.isArray(q) ? q : [];
  } catch {
    return [];
  }
}

function ReaderSkeleton() {
  return (
    <div className="mx-auto max-w-[44rem] space-y-4 pt-16">
      <div className="skeleton h-9 w-3/4" />
      <div className="skeleton h-4 w-1/3" />
      <div className="space-y-2.5 pt-6">
        {[100, 96, 92, 98, 70].map((w, i) => (
          <div key={i} className="skeleton h-4" style={{ width: `${w}%` }} />
        ))}
      </div>
      <div className="skeleton mt-6 h-56 w-full" />
    </div>
  );
}

function EmptyState({ busy, hasSources }: { busy: boolean; hasSources: boolean }) {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center py-28 text-center">
      <div className="mb-4 grid size-14 place-items-center rounded-2xl bg-accent-soft text-accent">
        <BookOpenText className="size-6" />
      </div>
      <h2 className="text-lg font-semibold tracking-tight">
        {busy ? "Your notes are on the way" : hasSources ? "Nothing generated yet" : "Add a PDF to get started"}
      </h2>
      <p className="mt-1.5 text-sm text-muted text-pretty">
        {busy
          ? "Every slide is being read, including tables, diagrams and code. The summary appears here first."
          : hasSources
            ? "Pick Summary in Study tools to generate notes from your slides."
            : "Use “Add PDF” in Sources. Several PDFs on one subject become one set of notes."}
      </p>
    </div>
  );
}
