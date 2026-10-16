"use client";

import { FileUp, Loader2 } from "lucide-react";
import { useRef, useState } from "react";

import { cn } from "@/lib/cn";
import { LIMITS } from "@/lib/limits";

export function pdfsFrom(list: FileList | File[]): { ok: File[]; rejected: string[] } {
  const ok: File[] = [];
  const rejected: string[] = [];
  for (const f of Array.from(list)) {
    if (!/\.pdf$/i.test(f.name) && f.type !== "application/pdf") rejected.push(`${f.name} isn't a PDF`);
    else if (f.size > LIMITS.maxPdfBytes) rejected.push(`${f.name} is over ${LIMITS.maxPdfBytes / 1048576} MB`);
    else ok.push(f);
  }
  return { ok, rejected };
}

export function Dropzone({
  onFiles,
  busy,
  busyLabel,
  compact = false,
}: {
  onFiles: (files: FileList) => void;
  busy?: boolean;
  busyLabel?: string;
  compact?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div
      role="button"
      tabIndex={0}
      aria-label="Upload lecture PDFs"
      onClick={() => !busy && input.current?.click()}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && !busy && input.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        if (!busy && e.dataTransfer.files.length) onFiles(e.dataTransfer.files);
      }}
      className={cn(
        "group relative flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed text-center transition-all outline-none",
        "focus-visible:ring-2 focus-visible:ring-accent/40",
        compact ? "gap-1.5 px-4 py-5" : "gap-3 px-6 py-14 sm:py-16",
        over ? "border-accent bg-accent-soft scale-[1.01]" : "border-border-strong bg-surface hover:border-accent/60 hover:bg-accent-soft/40",
        busy && "cursor-progress",
      )}
    >
      <input
        ref={input}
        type="file"
        accept="application/pdf,.pdf"
        multiple
        className="hidden"
        onChange={(e) => {
          if (e.target.files?.length) onFiles(e.target.files);
          e.target.value = "";
        }}
      />
      <span
        className={cn(
          "grid place-items-center rounded-2xl bg-accent-soft text-accent transition-transform group-hover:-translate-y-0.5",
          compact ? "size-9" : "size-14",
        )}
      >
        {busy ? <Loader2 className={cn("animate-spin", compact ? "size-4" : "size-6")} /> : <FileUp className={compact ? "size-4" : "size-6"} />}
      </span>
      {busy ? (
        <p className={cn("font-medium text-text", compact ? "text-[13px]" : "text-base")}>{busyLabel ?? "Uploading…"}</p>
      ) : compact ? (
        <p className="text-[13px] font-medium text-text">Add PDFs</p>
      ) : (
        <>
          <p className="text-lg font-semibold tracking-tight text-text">Drop your lecture PDFs here</p>
          <p className="max-w-sm text-sm text-muted">
            or <span className="font-medium text-accent-text underline underline-offset-4">browse files</span>. Several PDFs on one
            subject become one notebook. Up to {LIMITS.maxPdfBytes / 1048576} MB each.
          </p>
        </>
      )}
    </div>
  );
}
