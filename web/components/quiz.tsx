"use client";

import { Check, ChevronLeft, ChevronRight, Eye, RotateCcw, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Markdown } from "@/components/markdown";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

export type QuizChapter = { chapter: string; questions: { q: string; a: string }[] };
type Mark = "know" | "unsure";

export function Quiz({ notebookId, quiz }: { notebookId: string; quiz: QuizChapter[] }) {
  const storageKey = `quiz:${notebookId}`;
  const [marks, setMarks] = useState<Record<string, Mark>>({});
  const [chapter, setChapter] = useState(0);
  const [index, setIndex] = useState(0);
  const [revealed, setRevealed] = useState(false);

  useEffect(() => {
    try {
      setMarks(JSON.parse(localStorage.getItem(storageKey) ?? "{}"));
    } catch {
      /* fresh start */
    }
  }, [storageKey]);
  const save = (next: Record<string, Mark>) => {
    setMarks(next);
    try {
      localStorage.setItem(storageKey, JSON.stringify(next));
    } catch {
      /* not persisted */
    }
  };

  const total = useMemo(() => quiz.reduce((n, c) => n + c.questions.length, 0), [quiz]);
  const known = Object.values(marks).filter((m) => m === "know").length;

  if (!quiz.length) {
    return (
      <div className="mx-auto max-w-md py-24 text-center">
        <p className="font-medium">No quiz questions in these notes</p>
        <p className="mt-1 text-sm text-muted">The learner notes for this deck didn&apos;t include end-of-chapter questions.</p>
      </div>
    );
  }

  const ch = quiz[chapter];
  const q = ch.questions[index];
  const key = `${chapter}:${index}`;
  const go = (c: number, i: number) => {
    setChapter(c);
    setIndex(i);
    setRevealed(false);
  };
  const next = () => {
    if (index + 1 < ch.questions.length) go(chapter, index + 1);
    else if (chapter + 1 < quiz.length) go(chapter + 1, 0);
  };
  const prev = () => {
    if (index > 0) go(chapter, index - 1);
    else if (chapter > 0) go(chapter - 1, quiz[chapter - 1].questions.length - 1);
  };
  const mark = (m: Mark) => {
    save({ ...marks, [key]: m });
    next();
  };

  return (
    <div className="mx-auto max-w-[44rem] pb-24">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Quiz</h1>
          <p className="mt-1 text-sm text-muted">
            {known} of {total} marked as known
          </p>
        </div>
        <Button size="sm" variant="ghost" onClick={() => save({})}>
          <RotateCcw className="size-3.5" /> Reset
        </Button>
      </div>
      <div className="mb-5 h-1.5 overflow-hidden rounded-full bg-surface-2">
        <div className="h-full rounded-full bg-success transition-all" style={{ width: `${(known / total) * 100}%` }} />
      </div>

      <div className="mb-4 flex gap-2 overflow-x-auto pb-1">
        {quiz.map((c, ci) => {
          const done = c.questions.filter((_, qi) => marks[`${ci}:${qi}`] === "know").length;
          return (
            <button
              key={c.chapter + ci}
              onClick={() => go(ci, 0)}
              className={cn(
                "shrink-0 rounded-full border px-3 py-1.5 text-[12.5px] font-medium transition-colors",
                ci === chapter ? "border-accent bg-accent-soft text-accent-text" : "border-border bg-surface text-muted hover:text-text",
              )}
            >
              {c.chapter.replace(/^Chapter \d+ · /, "").slice(0, 40)} <span className="opacity-70">{done}/{c.questions.length}</span>
            </button>
          );
        })}
      </div>

      <div key={key} className="rise overflow-hidden rounded-2xl border border-border bg-surface shadow-card">
        <div className="flex items-center justify-between border-b border-border px-5 py-3 text-[12.5px] text-muted">
          <span className="truncate">{ch.chapter}</span>
          <span className="shrink-0">
            {index + 1} / {ch.questions.length}
          </span>
        </div>
        <div className="px-5 py-6 sm:px-7">
          <Markdown className="!text-[1.12rem] font-medium">{q.q}</Markdown>
          {revealed ? (
            <div className="rise mt-5 rounded-xl border border-border bg-surface-2 px-4 py-3">
              <Markdown compact>{q.a || "_No answer given in the notes._"}</Markdown>
            </div>
          ) : (
            <Button className="mt-6" variant="secondary" onClick={() => setRevealed(true)}>
              <Eye className="size-4" /> Show answer
            </Button>
          )}
        </div>
        <div className="flex items-center justify-between gap-2 border-t border-border bg-surface-2/50 px-3 py-2.5">
          <Button size="icon" variant="ghost" onClick={prev} aria-label="Previous question">
            <ChevronLeft className="size-4" />
          </Button>
          <div className="flex gap-2">
            <Button size="sm" variant={marks[key] === "unsure" ? "danger" : "secondary"} onClick={() => mark("unsure")}>
              <X className="size-3.5" /> Not yet
            </Button>
            <Button size="sm" variant={marks[key] === "know" ? "primary" : "secondary"} onClick={() => mark("know")}>
              <Check className="size-3.5" /> Got it
            </Button>
          </div>
          <Button size="icon" variant="ghost" onClick={next} aria-label="Next question">
            <ChevronRight className="size-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
