"use client";

import { ArrowLeft, BookOpenText, Cpu, PanelLeft } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { pdfsFrom } from "@/components/dropzone";
import { Quiz, type QuizChapter } from "@/components/quiz";
import { Reader, type SlideIndex } from "@/components/reader";
import { StatusBadge } from "@/components/status";
import { ACTIONS, ProgressCard, Sources, Studio, workerOnline, type View } from "@/components/studio";
import { ThemeToggle } from "@/components/theme-toggle";
import { api, ensureSession, supabase, uploadPdfs, type Doc, type Job, type Notebook, type Source, type WorkerStatus } from "@/lib/client";
import { cn } from "@/lib/cn";

async function fetchText(path: string): Promise<string> {
  const { data, error } = await supabase().storage.from("outputs").download(path);
  if (error || !data) throw new Error(error?.message ?? "Could not load the notes");
  return data.text();
}

export function NotebookView({ id }: { id: string }) {
  const [notebook, setNotebook] = useState<Notebook | null>(null);
  const [missing, setMissing] = useState(false);
  const [sources, setSources] = useState<Source[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [worker, setWorker] = useState<WorkerStatus | null>(null);
  const [view, setView] = useState<View>("summary");
  const [content, setContent] = useState<Record<string, string>>({}); // `${level}@${updated_at}` -> text
  const [slides, setSlides] = useState<SlideIndex | null>(null);
  const [mobilePane, setMobilePane] = useState<"notes" | "tools">("notes");
  const [uploading, setUploading] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const userPicked = useRef(false);

  const load = useCallback(async () => {
    await ensureSession();
    const db = supabase();
    const [nb, src, jb, dc, wk] = await Promise.all([
      db.from("notebooks").select("*").eq("id", id).maybeSingle(),
      db.from("sources").select("*").eq("notebook_id", id).order("position"),
      db.from("jobs").select("*").eq("notebook_id", id).order("created_at", { ascending: false }).limit(10),
      db.from("documents").select("*").eq("notebook_id", id),
      db.from("worker_status").select("*").order("last_seen", { ascending: false }).limit(1),
    ]);
    if (!nb.data) {
      setMissing(true);
      return;
    }
    setNotebook(nb.data as Notebook);
    setTitle((t) => t || (nb.data as Notebook).title);
    setSources((src.data ?? []) as Source[]);
    setJobs((jb.data ?? []) as Job[]);
    setDocs((dc.data ?? []) as Doc[]);
    setWorker(((wk.data ?? [])[0] as WorkerStatus) ?? null);
  }, [id]);

  useEffect(() => {
    load().catch((e) => toast.error(e instanceof Error ? e.message : "Could not load this notebook"));
    const db = supabase();
    const channel = db
      .channel(`notebook-${id}`)
      .on("postgres_changes", { event: "*", schema: "public", table: "jobs", filter: `notebook_id=eq.${id}` }, () => load())
      .on("postgres_changes", { event: "*", schema: "public", table: "documents", filter: `notebook_id=eq.${id}` }, () => load())
      .on("postgres_changes", { event: "UPDATE", schema: "public", table: "notebooks", filter: `id=eq.${id}` }, () => load())
      .on("postgres_changes", { event: "*", schema: "public", table: "worker_status" }, (p) => setWorker(p.new as WorkerStatus))
      .subscribe();
    // fallback refresh while something is in flight (Realtime may be blocked on some networks)
    const timer = setInterval(() => load().catch(() => {}), 15_000);
    return () => {
      db.removeChannel(channel);
      clearInterval(timer);
    };
  }, [id, load]);

  // open the first ready view (summary first) unless the user picked one
  useEffect(() => {
    if (userPicked.current) return;
    const first = ACTIONS.find((a) => docs.some((d) => d.level === (a.key === "quiz" ? "quiz" : a.level)));
    if (first) setView(first.key);
  }, [docs]);

  const level = view === "quiz" ? "quiz" : view;
  const doc = docs.find((d) => d.level === level);
  const docKey = doc ? `${level}@${doc.updated_at}` : null;

  useEffect(() => {
    if (!doc || !docKey || content[docKey] !== undefined) return;
    fetchText(doc.storage_path)
      .then((text) => setContent((c) => ({ ...c, [docKey]: text })))
      .catch((e) => toast.error(e.message));
  }, [doc, docKey, content]);

  const slidesStamp = docs.find((d) => d.level === "learner")?.updated_at;
  useEffect(() => {
    if (!slidesStamp) return;
    fetchText(`${id}/slides.json`)
      .then((t) => setSlides(JSON.parse(t)))
      .catch(() => setSlides(null));
  }, [id, slidesStamp]);

  const generate = async (lvl: string) => {
    try {
      await api("/api/jobs", { notebook_id: id, levels: lvl === "summary" || lvl === "notes" ? [lvl, "learner"] : [lvl] });
      toast.success("Queued. It'll appear here when it's ready.");
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
      // regenerate what exists so far; slides and sections already done are reused (cached)
      const levels = [...new Set(docs.map((d) => (d.level === "quiz" ? "learner" : d.level)).concat(["summary", "learner"]))];
      await api("/api/jobs", { notebook_id: id, levels });
      toast.success("Added. Updating your notes…");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(null);
      load();
    }
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

  const text = docKey ? content[docKey] : undefined;
  const busyJob = jobs.some((j) => j.status === "queued" || j.status === "running");
  const online = workerOnline(worker);

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
          className="grid size-9 place-items-center rounded-lg text-muted hover:bg-surface-2 lg:hidden"
          onClick={() => setMobilePane((p) => (p === "notes" ? "tools" : "notes"))}
          aria-label="Toggle sources and tools"
        >
          <PanelLeft className="size-[18px]" />
        </button>
        <ThemeToggle />
      </header>

      <div className="mx-auto flex w-full max-w-[1400px] flex-1">
        <aside
          className={cn(
            "no-print w-full shrink-0 border-border bg-bg px-4 py-5 lg:sticky lg:top-14 lg:block lg:h-[calc(100dvh-56px)] lg:w-[320px] lg:overflow-y-auto lg:border-r",
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
              docs={docs}
              jobs={jobs}
              view={view}
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

        <main className={cn("min-w-0 flex-1 px-4 sm:px-8", mobilePane === "tools" ? "max-lg:hidden" : "")}>
          {!notebook ? (
            <ReaderSkeleton />
          ) : !doc ? (
            <EmptyState busy={busyJob} hasSources={sources.length > 0} />
          ) : text === undefined ? (
            <ReaderSkeleton />
          ) : view === "quiz" ? (
            <div className="pt-8">
              <Quiz notebookId={id} quiz={safeQuiz(text)} />
            </div>
          ) : (
            <Reader key={docKey} notebookId={id} level={level} md={text} slides={slides} />
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
