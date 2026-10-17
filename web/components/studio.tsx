"use client";

import { AlertTriangle, BookOpen, CircleHelp, FileText, ListChecks, Loader2, Plus, Smile, WifiOff, Zap } from "lucide-react";

import { Dropzone } from "@/components/dropzone";
import { Button } from "@/components/ui/button";
import type { Doc, Job, Source, WorkerStatus } from "@/lib/client";
import { cn } from "@/lib/cn";
import { LIMITS } from "@/lib/limits";

export type View = "summary" | "learner" | "proficient" | "dockey" | "quiz";

export const ACTIONS: { key: View; label: string; hint: string; icon: typeof Zap; level: string }[] = [
  { key: "summary", label: "Summary", hint: "The short version", icon: Zap, level: "summary" },
  { key: "learner", label: "Learner notes", hint: "Full explanations", icon: BookOpen, level: "learner" },
  { key: "proficient", label: "Revision sheet", hint: "Dense, for revising", icon: ListChecks, level: "proficient" },
  { key: "dockey", label: "Explain simply", hint: "Like I'm five", icon: Smile, level: "dockey" },
  { key: "quiz", label: "Quiz", hint: "Test yourself", icon: CircleHelp, level: "learner" },
];

const STAGES: Record<string, string> = {
  starting: "Starting up",
  requeued: "Waiting to resume",
  ingest: "Reading the PDFs",
  extract: "Reading slides",
  outline: "Grouping slides into topics",
  "write:learner": "Writing learner notes",
  "write:proficient": "Writing the revision sheet",
  "write:dockey": "Writing the simple version",
  summary: "Summarising",
  done: "Finishing",
};

export function workerOnline(w: WorkerStatus | null): boolean {
  return !!w && Date.now() - new Date(w.last_seen).getTime() < LIMITS.workerFreshMs * 2;
}

export function Studio({
  docs,
  jobs,
  view,
  onSelect,
  onGenerate,
}: {
  docs: Doc[];
  jobs: Job[];
  view: View;
  onSelect: (v: View) => void;
  onGenerate: (level: string) => void;
}) {
  const active = jobs.filter((j) => j.status === "queued" || j.status === "running");
  return (
    <div className="grid grid-cols-2 gap-2">
      {ACTIONS.map(({ key, label, hint, icon: Icon, level }) => {
        const ready = docs.some((d) => d.level === (key === "quiz" ? "quiz" : level));
        const generating = !ready && active.some((j) => j.levels.includes(level));
        const selected = view === key && ready;
        return (
          <button
            key={key}
            onClick={() => (ready ? onSelect(key) : !generating && onGenerate(level))}
            disabled={generating}
            className={cn(
              "group relative flex flex-col items-start gap-2 rounded-xl border p-3 text-left transition-all",
              key === "summary" && "col-span-2",
              selected
                ? "border-accent bg-accent-soft"
                : "border-border bg-surface hover:-translate-y-px hover:border-border-strong hover:shadow-card",
              generating && "cursor-progress",
            )}
          >
            <div className="flex w-full items-center justify-between">
              <span className={cn("grid size-7 place-items-center rounded-lg", selected ? "bg-accent text-white dark:text-[#0e0e10]" : "bg-surface-2 text-muted group-hover:text-accent")}>
                <Icon className="size-4" />
              </span>
              {generating && <Loader2 className="size-3.5 animate-spin text-accent" />}
            </div>
            <div>
              <span className="block text-[13.5px] leading-tight font-semibold tracking-tight whitespace-nowrap">{label}</span>
              <span className="mt-0.5 block text-[12px] leading-tight text-muted">
                {ready ? hint : generating ? "Generating…" : <span className="font-medium text-accent-text">Generate →</span>}
              </span>
            </div>
          </button>
        );
      })}
    </div>
  );
}

export function ProgressCard({ jobs, worker, onRetry }: { jobs: Job[]; worker: WorkerStatus | null; onRetry: (job: Job) => void }) {
  const job = jobs.find((j) => j.status === "running") ?? jobs.find((j) => j.status === "queued");
  const latest = jobs[0];
  if (!job) {
    if (latest?.status === "failed") {
      return (
        <div className="rounded-xl border border-danger/30 bg-danger-soft p-3.5">
          <div className="flex items-center gap-2 text-[13px] font-semibold text-danger">
            <AlertTriangle className="size-4" /> Generation failed
          </div>
          <p className="mt-1 line-clamp-4 text-[12.5px] text-danger/90">{latest.error ?? "Unknown error"}</p>
          <Button size="sm" variant="secondary" className="mt-2.5" onClick={() => onRetry(latest)}>
            Try again
          </Button>
        </div>
      );
    }
    return null;
  }
  const online = workerOnline(worker);
  const pct = job.total ? Math.min(100, Math.round((job.done / job.total) * 100)) : 0;
  const label = job.status === "queued" ? (online ? "Waiting in the queue" : "Queued") : (STAGES[job.stage ?? ""] ?? "Working");
  return (
    <div className="rounded-xl border border-border bg-surface p-3.5 shadow-card">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-[13px] font-semibold">
          <Loader2 className="size-3.5 animate-spin text-accent" /> {label}
        </span>
        {job.status === "running" && job.total > 0 && (
          <span className="text-[12px] tabular-nums text-muted">
            {job.done}/{job.total}
          </span>
        )}
      </div>
      <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-surface-2">
        <div
          className={cn("h-full rounded-full bg-accent transition-all duration-500", job.status === "queued" && "w-1/4 animate-pulse")}
          style={job.status === "running" ? { width: `${Math.max(4, pct)}%` } : undefined}
        />
      </div>
      {!online ? (
        <p className="mt-2.5 flex items-start gap-1.5 text-[12px] text-warning">
          <WifiOff className="mt-px size-3.5 shrink-0" /> The generator is offline. Your job will start as soon as it&apos;s back.
        </p>
      ) : (
        <p className="mt-2 text-[12px] text-faint">
          Big decks take a few minutes. You can leave this page{job.cost_usd > 0 ? ` · $${Number(job.cost_usd).toFixed(3)} so far` : ""}.
        </p>
      )}
    </div>
  );
}

export function Sources({
  sources,
  onAdd,
  busy,
}: {
  sources: Source[];
  onAdd: (files: FileList) => void;
  busy: string | null;
}) {
  return (
    <div className="space-y-1.5">
      {sources.map((s) => (
        <div key={s.id} className="flex items-center gap-2.5 rounded-lg px-1.5 py-1.5">
          <span className="grid size-7 shrink-0 place-items-center rounded-md bg-danger-soft text-danger">
            <FileText className="size-3.5" />
          </span>
          <div className="min-w-0">
            <p className="truncate text-[13px] font-medium" title={s.filename}>
              {s.filename}
            </p>
            <p className="text-[11.5px] text-faint">
              {(s.size_bytes / 1048576).toFixed(1)} MB{s.pages ? ` · ${s.pages} slides` : ""}
            </p>
          </div>
        </div>
      ))}
      {sources.length < LIMITS.maxPdfsPerNotebook &&
        (busy ? (
          <Dropzone compact busy busyLabel={busy} onFiles={onAdd} />
        ) : (
          <label className="flex cursor-pointer items-center gap-2 rounded-lg px-1.5 py-1.5 text-[13px] font-medium text-muted hover:bg-surface-2 hover:text-text">
            <span className="grid size-7 place-items-center rounded-md border border-dashed border-border-strong">
              <Plus className="size-3.5" />
            </span>
            Add PDF
            <input
              type="file"
              accept="application/pdf,.pdf"
              multiple
              className="hidden"
              onChange={(e) => {
                if (e.target.files?.length) onAdd(e.target.files);
                e.target.value = "";
              }}
            />
          </label>
        ))}
    </div>
  );
}
