"use client";

import { Check, Copy, Cpu, Cloud, CornerDownLeft, Loader2, Sparkles, Wand2, X } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { Markdown } from "@/components/markdown";
import { Button } from "@/components/ui/button";
import { apiRaw, supabase } from "@/lib/client";
import { cn } from "@/lib/cn";
import { LIMITS } from "@/lib/limits";

export type ExplainTarget = {
  section: number;
  selection?: string;
  image?: string; // "assets/…png" or "slide:A·4"
  label?: string; // what to show as the quoted selection
  rect?: { x: number; y: number; w: number; h: number }; // where it was picked (viewport coords)
};
type Turn = { role: "user" | "assistant"; content: string };
type Phase = "thinking" | "streaming" | "done" | "error";
const MAX_FOLLOWUPS = 3;

export function ExplainPanel({
  notebookId,
  level,
  target,
  onClose,
}: {
  notebookId: string;
  level: string;
  target: ExplainTarget;
  onClose: () => void;
}) {
  const [history, setHistory] = useState<Turn[]>([]); // completed turns after the first answer
  const [text, setText] = useState("");
  const [phase, setPhase] = useState<Phase>("thinking");
  const [error, setError] = useState<string | null>(null);
  const [source, setSource] = useState<"local" | "cloud" | null>(null);
  const [question, setQuestion] = useState("");
  const [copied, setCopied] = useState(false);
  const cancel = useRef<() => void>(() => {});
  const lastRun = useRef<{ turns: Turn[]; simpler: boolean }>({ turns: [], simpler: false });
  const panel = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);

  const run = useCallback(
    async (turns: Turn[], simpler: boolean) => {
      cancel.current();
      lastRun.current = { turns, simpler };
      let cancelled = false;
      const cleanups: (() => void)[] = [];
      cancel.current = () => {
        cancelled = true;
        cleanups.forEach((f) => f());
      };
      setText("");
      setError(null);
      setPhase("thinking");
      const payload = {
        notebook_id: notebookId,
        level,
        section: target.section,
        selection: target.selection ?? null,
        image: target.image ?? null,
        history: turns,
        simpler,
      };

      const streamFrom = async (res: Response) => {
        setSource("cloud");
        const reader = res.body!.getReader();
        const dec = new TextDecoder();
        let acc = "";
        cleanups.push(() => reader.cancel().catch(() => {}));
        for (;;) {
          const { done, value } = await reader.read();
          if (done || cancelled) break;
          acc += dec.decode(value, { stream: true });
          setText(acc);
          setPhase("streaming");
        }
        if (!cancelled) setPhase(acc.trim() ? "done" : "error");
        if (!acc.trim() && !cancelled) setError("No answer came back, try again.");
      };

      try {
        const res = await apiRaw("/api/explain", payload);
        if (cancelled) return;
        if (res.headers.get("content-type")?.startsWith("text/plain")) return await streamFrom(res);
        const json = (await res.json()) as { answer?: string; source?: "local" | "cloud"; request_id?: string };
        if (json.answer) {
          setText(json.answer);
          setSource(json.source ?? null);
          setPhase("done");
          return;
        }
        const id = json.request_id!;
        setSource("local");
        let started = false;
        const apply = (row: { status: string; partial_text?: string; answer?: string; error?: string }) => {
          if (cancelled) return;
          if (row.status !== "queued") started = true;
          if (row.partial_text) {
            setText(row.partial_text);
            setPhase("streaming");
          }
          if (row.status === "done") {
            setText(row.answer ?? row.partial_text ?? "");
            setPhase("done");
            cancel.current();
          } else if (row.status === "failed" && !/cloud fallback/.test(row.error ?? "")) {
            setError(row.error ?? "The explanation failed");
            setPhase("error");
            cancel.current();
          }
        };
        const channel = supabase()
          .channel(`explain-${id}`)
          .on("postgres_changes", { event: "UPDATE", schema: "public", table: "explain_requests", filter: `id=eq.${id}` },
            (p) => apply(p.new as never))
          .subscribe();
        cleanups.push(() => supabase().removeChannel(channel));
        // Realtime can miss a beat: poll too
        const poll = setInterval(async () => {
          const { data } = await supabase().from("explain_requests").select("status, partial_text, answer, error").eq("id", id).maybeSingle();
          if (data) apply(data);
        }, 1500);
        cleanups.push(() => clearInterval(poll));
        // local model didn't start in time (busy/asleep): hand over to the cloud
        const handoff = setTimeout(async () => {
          if (started || cancelled) return;
          clearInterval(poll);
          supabase().removeChannel(channel);
          try {
            const res2 = await apiRaw("/api/explain", { ...payload, request_id: id, force_cloud: true });
            if (cancelled) return;
            if (res2.headers.get("content-type")?.startsWith("text/plain")) return await streamFrom(res2);
            const j = (await res2.json()) as { answer?: string; source?: "local" | "cloud" };
            if (j.answer) {
              setText(j.answer);
              setSource(j.source ?? "cloud");
              setPhase("done");
            }
          } catch (e) {
            setError(e instanceof Error ? e.message : "The explanation failed");
            setPhase("error");
          }
        }, LIMITS.explainWaitMs);
        cleanups.push(() => clearTimeout(handoff));
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "The explanation failed");
        setPhase("error");
      }
    },
    [notebookId, level, target],
  );

  useEffect(() => {
    setHistory([]);
    run([], false);
    return () => cancel.current();
  }, [run]);

  // place next to the selection, kept inside the viewport (bottom sheet on phones)
  useLayoutEffect(() => {
    const el = panel.current;
    if (!el || window.innerWidth < 640) {
      setPos(null);
      return;
    }
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const r = target.rect ?? { x: window.innerWidth / 2, y: 120, w: 0, h: 0 };
    let left = Math.min(Math.max(16, r.x + r.w / 2 - w / 2), window.innerWidth - w - 16);
    let top = r.y + r.h + 10;
    if (top + h > window.innerHeight - 16) top = Math.max(16, r.y - h - 10);
    if (top < 16) top = 16;
    if (left < 16) left = 16;
    setPos({ left, top });
  }, [target, text, phase]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const answerSoFar = () => [...history, { role: "assistant" as const, content: text }];
  const followUps = history.filter((t) => t.role === "user").length;

  const ask = () => {
    const q = question.trim();
    if (!q || phase === "thinking" || phase === "streaming") return;
    const turns = [...answerSoFar(), { role: "user" as const, content: q }];
    setHistory(turns);
    setQuestion("");
    run(turns, false);
  };
  const simpler = () => {
    const turns = answerSoFar();
    setHistory(turns);
    run(turns, true);
  };

  const busy = phase === "thinking" || phase === "streaming";
  return (
    <div
      ref={panel}
      role="dialog"
      aria-label="Explanation"
      style={pos ? { left: pos.left, top: pos.top } : undefined}
      className={cn(
        "no-print rise fixed z-50 flex flex-col overflow-hidden border border-border bg-surface shadow-pop",
        pos
          ? "w-[400px] max-h-[min(560px,calc(100dvh-32px))] rounded-2xl"
          : "inset-x-0 bottom-0 max-h-[75dvh] rounded-t-2xl sm:inset-x-auto sm:right-6 sm:bottom-6 sm:w-[400px] sm:rounded-2xl",
      )}
    >
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
        <div className="flex items-center gap-2 text-[13px] font-semibold">
          <span className="grid size-6 place-items-center rounded-md bg-accent-soft text-accent">
            <Sparkles className="size-3.5" />
          </span>
          Explain
          {source && phase !== "thinking" && (
            <span className="inline-flex items-center gap-1 rounded-full bg-surface-2 px-2 py-0.5 text-[11px] font-medium text-muted"
              title={source === "local" ? "Answered by the local model" : "Answered by a cloud model"}>
              {source === "local" ? <Cpu className="size-3" /> : <Cloud className="size-3" />}
              {source}
            </span>
          )}
        </div>
        <Button variant="ghost" size="icon" className="size-7" onClick={onClose} aria-label="Close">
          <X className="size-4" />
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        <p className="mb-3 line-clamp-3 border-l-2 border-accent/50 pl-3 font-serif text-[13.5px] italic leading-snug text-muted">
          {target.label ?? target.selection ?? "Selected figure"}
        </p>
        {history.filter((t) => t.role === "user").slice(-1).map((t) => (
          <p key={t.content} className="mb-2 text-[13px] font-medium text-text">
            {t.content}
          </p>
        ))}
        {phase === "thinking" ? (
          <div className="space-y-2 py-1" aria-live="polite">
            <div className="skeleton h-3.5 w-11/12" />
            <div className="skeleton h-3.5 w-full" />
            <div className="skeleton h-3.5 w-3/4" />
            <p className="pt-1 text-[12px] text-faint">{source === "local" ? "Waking up the local model…" : "Thinking…"}</p>
          </div>
        ) : phase === "error" ? (
          <div className="rounded-xl bg-danger-soft px-3 py-2.5 text-[13px] text-danger">
            {error}
            <button className="ml-2 font-semibold underline" onClick={() => run(lastRun.current.turns, lastRun.current.simpler)}>
              Retry
            </button>
          </div>
        ) : (
          <div className={cn(phase === "streaming" && "caret")} aria-live="polite">
            <Markdown compact>{text}</Markdown>
          </div>
        )}
      </div>

      <div className="space-y-2 border-t border-border bg-surface-2/60 px-3 py-2.5">
        <div className="flex items-center gap-1.5">
          <Button size="sm" variant="secondary" disabled={busy || !text} onClick={simpler}>
            <Wand2 className="size-3.5" /> Simpler
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={!text}
            onClick={() => {
              navigator.clipboard.writeText(text).then(() => {
                setCopied(true);
                setTimeout(() => setCopied(false), 1200);
              });
            }}
          >
            {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />} {copied ? "Copied" : "Copy"}
          </Button>
          {busy && <Loader2 className="ml-auto size-4 animate-spin text-faint" />}
        </div>
        {followUps < MAX_FOLLOWUPS ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              ask();
            }}
            className="flex items-center gap-1.5 rounded-xl border border-border bg-surface px-2.5 focus-within:border-accent/60"
          >
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Ask a follow-up…"
              maxLength={300}
              className="h-9 min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-faint"
            />
            <button type="submit" disabled={!question.trim() || busy} className="text-faint hover:text-accent disabled:opacity-40" aria-label="Ask">
              <CornerDownLeft className="size-4" />
            </button>
          </form>
        ) : (
          <p className="px-1 text-[12px] text-faint">Follow-up limit reached. Select new text to ask again.</p>
        )}
      </div>
    </div>
  );
}
