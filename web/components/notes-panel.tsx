"use client";

import { Check, Eye, Loader2, NotebookPen, Pencil, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Markdown } from "@/components/markdown";
import { supabase, type Note } from "@/lib/client";
import { cn } from "@/lib/cn";
import { sectionKey } from "@/lib/library";

export const MAX_NOTE = 20000;
const SAVE_DELAY = 800;

export type NotesApi = {
  notes: Record<string, Note>; // by sectionKey(chapter, section)
  save: (chapterId: string, section: number, body: string) => Promise<void>;
};

function lsKey(notebookId: string) {
  return `notes:${notebookId}`;
}
function readLocal(notebookId: string): Record<string, Note> {
  try {
    return JSON.parse(localStorage.getItem(lsKey(notebookId)) ?? "{}");
  } catch {
    return {};
  }
}
function writeLocal(notebookId: string, notes: Record<string, Note>) {
  try {
    localStorage.setItem(lsKey(notebookId), JSON.stringify(notes));
  } catch {
    /* private mode: just not remembered */
  }
}

/** The notebook's notes, one per section, synced to Supabase (the demo keeps them in this browser only). */
export function useNotes(notebookId: string, demo: boolean): NotesApi {
  const [notes, setNotes] = useState<Record<string, Note>>({});

  useEffect(() => {
    if (demo) return setNotes(readLocal(notebookId));
    let live = true;
    supabase()
      .from("user_notes")
      .select("chapter_id, section, body, updated_at")
      .eq("notebook_id", notebookId)
      .then(({ data }) => {
        if (!live) return;
        setNotes(Object.fromEntries(((data ?? []) as Note[]).map((n) => [sectionKey(n.chapter_id, n.section), n])));
      });
    return () => {
      live = false;
    };
  }, [notebookId, demo]);

  const save = useCallback(
    async (chapterId: string, section: number, body: string) => {
      const key = sectionKey(chapterId, section);
      const row: Note = { chapter_id: chapterId, section, body, updated_at: new Date().toISOString() };
      setNotes((prev) => {
        const next = { ...prev };
        if (body.trim()) next[key] = row;
        else delete next[key];
        if (demo) writeLocal(notebookId, next);
        return next;
      });
      if (demo) return;
      const db = supabase();
      const where = { notebook_id: notebookId, chapter_id: chapterId, section };
      const { error } = body.trim()
        ? await db.from("user_notes").upsert({ ...where, body, updated_at: row.updated_at }, { onConflict: "notebook_id,chapter_id,section" })
        : await db.from("user_notes").delete().match(where);
      if (error) throw new Error(error.message);
    },
    [notebookId, demo],
  );
  return { notes, save };
}

export type NoteTarget = { chapterId: string; section: number; chapterTitle: string; title: string };

type SaveState = "saved" | "saving" | "error";

/** Notes for the open topic: autosaved textarea with a Markdown preview, and a browser-side draft as backup. */
export function NotesPanel({
  notebookId,
  target,
  api,
  onClose,
}: {
  notebookId: string;
  target: NoteTarget;
  api: NotesApi;
  onClose: () => void;
}) {
  const key = sectionKey(target.chapterId, target.section);
  const saved = api.notes[key]?.body ?? "";
  const draftKey = `notedraft:${notebookId}:${key}`;
  const [text, setText] = useState(saved);
  const [state, setState] = useState<SaveState>("saved");
  const [preview, setPreview] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const dirty = useRef(false);
  const latest = useRef({ text, target, api });
  latest.current = { text, target, api };

  const flush = useCallback(async () => {
    clearTimeout(timer.current);
    if (!dirty.current) return;
    dirty.current = false;
    const { text: body, target: t, api: a } = latest.current;
    setState("saving");
    try {
      await a.save(t.chapterId, t.section, body);
      try {
        localStorage.removeItem(`notedraft:${notebookId}:${sectionKey(t.chapterId, t.section)}`);
      } catch {
        /* ignore */
      }
      setState("saved");
    } catch {
      dirty.current = true;
      setState("error");
    }
  }, [notebookId]);

  // switching topic: save the one we leave, load the next (a leftover unsaved draft wins over the server copy)
  useEffect(() => {
    let draft: string | null = null;
    try {
      draft = localStorage.getItem(draftKey);
    } catch {
      /* ignore */
    }
    dirty.current = false;
    setText(draft ?? saved);
    setState(draft !== null && draft !== saved ? "error" : "saved");
    dirty.current = draft !== null && draft !== saved;
    setPreview(false);
    return () => {
      void flush();
    };
    // `saved` is only the starting value for this topic; later server updates must not overwrite typing
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  // a note arriving after load (first fetch) fills an untouched box
  useEffect(() => {
    if (!dirty.current) setText(saved);
  }, [saved]);

  useEffect(() => {
    const onHide = () => void flush();
    window.addEventListener("pagehide", onHide);
    return () => window.removeEventListener("pagehide", onHide);
  }, [flush]);

  const change = (value: string) => {
    const body = value.slice(0, MAX_NOTE);
    setText(body);
    dirty.current = true;
    setState("saving");
    try {
      localStorage.setItem(draftKey, body);
    } catch {
      /* ignore */
    }
    clearTimeout(timer.current);
    timer.current = setTimeout(() => void flush(), SAVE_DELAY);
  };

  return (
    <aside aria-label="My notes" className="flex h-full min-h-0 flex-col rounded-xl border border-border bg-surface shadow-card max-xl:max-h-[70dvh]">
      <div className="flex items-start gap-2 border-b border-border px-3.5 py-2.5">
        <NotebookPen className="mt-0.5 size-4 shrink-0 text-accent" />
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-semibold">My notes</p>
          <p className="truncate text-[12px] text-faint" title={`${target.chapterTitle} · ${target.title}`}>
            {target.title}
          </p>
        </div>
        <button
          onClick={() => setPreview((p) => !p)}
          aria-pressed={preview}
          title={preview ? "Edit" : "Preview"}
          className="grid size-8 place-items-center rounded-md text-muted hover:bg-surface-2 hover:text-text"
        >
          {preview ? <Pencil className="size-3.5" /> : <Eye className="size-3.5" />}
        </button>
        <button onClick={onClose} aria-label="Close notes" className="grid size-8 place-items-center rounded-md text-muted hover:bg-surface-2 hover:text-text">
          <X className="size-4" />
        </button>
      </div>
      <div className="min-h-0 flex-1 p-3">
        {preview ? (
          <div className="h-full overflow-y-auto">{text.trim() ? <Markdown compact>{text}</Markdown> : <p className="text-sm text-faint">Nothing written yet.</p>}</div>
        ) : (
          <textarea
            value={text}
            onChange={(e) => change(e.target.value)}
            onBlur={() => void flush()}
            placeholder="Your thoughts on this topic. What's confusing, what to ask, how it connects… Markdown works."
            aria-label={`Notes on ${target.title}`}
            className="block h-full min-h-[220px] w-full resize-none rounded-lg bg-transparent text-[14px] leading-relaxed outline-none placeholder:text-faint"
          />
        )}
      </div>
      <div className="flex items-center justify-between border-t border-border px-3.5 py-2 text-[11.5px] text-faint">
        <span className={cn("flex items-center gap-1.5", state === "error" && "text-danger")} role="status">
          {state === "saving" && <Loader2 className="size-3 animate-spin" />}
          {state === "saved" && <Check className="size-3 text-success" />}
          {state === "saved" ? "Saved" : state === "saving" ? "Saving…" : "Not saved yet. Will retry"}
        </span>
        <span className="tabular-nums">{text.length.toLocaleString()} / {MAX_NOTE.toLocaleString()}</span>
      </div>
    </aside>
  );
}
