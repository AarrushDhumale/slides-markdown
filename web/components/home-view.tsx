"use client";

import { BookOpenText, Layers, Sparkles, Wand2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Dropzone, pdfsFrom } from "@/components/dropzone";
import { Logo } from "@/components/logo";
import { StatusBadge, timeAgo } from "@/components/status";
import { ThemeToggle } from "@/components/theme-toggle";
import { api, ensureSession, supabase, uploadPdfs, type Notebook } from "@/lib/client";

type Row = Notebook & { sources: { count: number }[] };

function titleFor(files: File[]): string {
  const stems = files.map((f) => f.name.replace(/\.pdf$/i, "").replace(/[_]+/g, " ").trim());
  if (stems.length === 1) return stems[0];
  // common prefix of several decks ("OS Lecture 3", "OS Lecture 4" -> "OS Lecture")
  let prefix = stems[0];
  for (const s of stems) while (!s.startsWith(prefix)) prefix = prefix.slice(0, -1);
  prefix = prefix.replace(/[\s\-–:,.(]+\d*$/, "").trim();
  return prefix.length >= 3 ? prefix : `${stems[0]} + ${stems.length - 1} more`;
}

export function HomeView() {
  const router = useRouter();
  const [rows, setRows] = useState<Row[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      await ensureSession();
      const { data, error } = await supabase()
        .from("notebooks")
        .select("*, sources(count)")
        .order("updated_at", { ascending: false });
      if (error) throw error;
      setRows(data as Row[]);
    } catch (e) {
      setRows([]);
      toast.error(e instanceof Error ? e.message : "Could not load your notebooks");
    }
  }, []);

  useEffect(() => {
    load();
    const onFocus = () => load();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [load]);

  const onFiles = async (list: FileList) => {
    const { ok, rejected } = pdfsFrom(list);
    rejected.forEach((r) => toast.error(r));
    if (!ok.length) return;
    try {
      setBusy("Creating notebook…");
      const { id } = await api<{ id: string }>("/api/notebooks", { title: titleFor(ok) });
      await uploadPdfs(id, ok, (name) => setBusy(`Uploading ${name}…`));
      setBusy("Starting…");
      await api("/api/jobs", { notebook_id: id, levels: ["summary", "learner"] });
      router.push(`/notebooks/${id}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Upload failed");
      setBusy(null);
      load();
    }
  };

  return (
    <div className="min-h-dvh">
      <header className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6">
        <Logo />
        <ThemeToggle />
      </header>

      <main className="mx-auto max-w-6xl px-4 pb-24 sm:px-6">
        <section className="relative mx-auto max-w-3xl pt-10 pb-12 text-center sm:pt-20">
          <p className="eyebrow relative">Slides → study notes</p>
          <h1 className="relative mt-5 text-[2.4rem] leading-[1.05] font-semibold tracking-[-0.035em] text-balance sm:text-6xl">
            Turn lecture slides into notes you&apos;ll actually read.
          </h1>
          <p className="relative mx-auto mt-5 max-w-lg text-[15px] leading-relaxed text-muted text-pretty sm:text-base">
            Upload a deck. Get a summary first, then full notes, a revision sheet, an explain-it-simply version and a
            quiz, all grounded in your slides.
          </p>
        </section>

        <div className="mx-auto max-w-2xl">
          <Dropzone onFiles={onFiles} busy={busy !== null} busyLabel={busy ?? undefined} />
          <div className="mt-5 grid grid-cols-1 gap-3 text-[13px] text-muted sm:grid-cols-3">
            {[
              { icon: Layers, text: "Reads every slide: tables, diagrams, code and math" },
              { icon: BookOpenText, text: "Summary first; deeper versions one click away" },
              { icon: Wand2, text: "Highlight anything to get it explained" },
            ].map(({ icon: Icon, text }) => (
              <div key={text} className="flex items-center gap-2.5 rounded-xl px-1 sm:justify-center sm:text-center">
                <Icon className="size-4 shrink-0 text-accent" />
                <span>{text}</span>
              </div>
            ))}
          </div>
        </div>

        <section className="mt-16">
          <div className="mb-4 flex items-baseline justify-between">
            <h2 className="text-lg font-semibold tracking-tight">Your notebooks</h2>
            {rows && rows.length > 0 && <span className="text-sm text-muted">{rows.length}</span>}
          </div>
          {rows === null ? (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {[0, 1, 2].map((i) => (
                <div key={i} className="skeleton h-[132px]" />
              ))}
            </div>
          ) : rows.length === 0 ? (
            <div className="rounded-2xl border border-border bg-surface px-6 py-12 text-center">
              <div className="mx-auto mb-3 grid size-11 place-items-center rounded-xl bg-surface-2 text-faint">
                <BookOpenText className="size-5" />
              </div>
              <p className="font-medium">No notebooks yet</p>
              <p className="mt-1 text-sm text-muted">Drop a lecture PDF above to make your first one.</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {rows.map((nb, i) => (
                <Link
                  key={nb.id}
                  href={`/notebooks/${nb.id}`}
                  style={{ animationDelay: `${Math.min(i, 8) * 30}ms` }}
                  className="rise group flex flex-col justify-between rounded-2xl border border-border bg-surface p-5 shadow-card transition-all hover:-translate-y-0.5 hover:border-border-strong hover:shadow-pop"
                >
                  <div className="flex items-start gap-3">
                    <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent">
                      <BookOpenText className="size-5" />
                    </span>
                    <div className="min-w-0">
                      <h3 className="line-clamp-2 font-semibold leading-snug tracking-tight">{nb.title}</h3>
                      <p className="mt-0.5 text-[13px] text-muted">
                        {nb.sources?.[0]?.count ?? 0} PDF{nb.sources?.[0]?.count === 1 ? "" : "s"} · {timeAgo(nb.updated_at)}
                      </p>
                    </div>
                  </div>
                  <div className="mt-5 flex items-center justify-between">
                    <StatusBadge status={nb.status} />
                    <span className="text-[13px] font-medium text-accent-text opacity-0 transition-opacity group-hover:opacity-100">
                      Open →
                    </span>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
