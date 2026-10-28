"use client";

import { Download, NotebookPen } from "lucide-react";

import { Markdown } from "@/components/markdown";
import { Button } from "@/components/ui/button";
import type { LibraryBook } from "@/lib/book";
import { sectionKey } from "@/lib/library";
import type { NotesApi } from "@/components/notes-panel";

/** Everything the student wrote, in reading order (chapter, then topic), with a jump link to each topic. */
export function MyNotes({ book, notes, title, onOpen }: { book: LibraryBook; notes: NotesApi["notes"]; title: string; onOpen: (chapterIdx: number, section: number) => void }) {
  const groups = book.chapters
    .map((c, chapterIdx) => ({
      chapterIdx,
      title: c.title,
      items: c.pages.filter((p) => notes[sectionKey(c.id, p.index)]).map((p) => ({ page: p, body: notes[sectionKey(c.id, p.index)].body })),
    }))
    .filter((g) => g.items.length);
  const count = groups.reduce((n, g) => n + g.items.length, 0);

  const download = () => {
    const md = [`# My notes: ${title}`, ...groups.flatMap((g) => [`## ${g.title}`, ...g.items.map((i) => `### ${i.page.title}\n\n${i.body.trim()}`)])].join("\n\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([md], { type: "text/markdown" }));
    a.download = `${title.replace(/[^\w -]+/g, "").trim() || "notebook"} - my notes.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  if (!count) {
    return (
      <div className="mx-auto flex max-w-md flex-col items-center py-28 text-center">
        <div className="mb-4 grid size-14 place-items-center rounded-2xl bg-accent-soft text-accent">
          <NotebookPen className="size-6" />
        </div>
        <h2 className="text-lg font-semibold tracking-tight">No notes yet</h2>
        <p className="mt-1.5 text-sm text-muted text-pretty">Open any topic and press Notes to write down your thoughts. They show up here, grouped by chapter.</p>
      </div>
    );
  }
  return (
    <div className="mx-auto max-w-[44rem] pt-8 pb-24">
      <div className="mb-6 flex items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">My notes</h1>
          <p className="mt-1 text-sm text-muted">{count} topic{count === 1 ? "" : "s"} with notes</p>
        </div>
        <Button size="sm" variant="ghost" onClick={download}>
          <Download className="size-4" /> .md
        </Button>
      </div>
      {groups.map((g) => (
        <section key={g.chapterIdx} className="mb-8">
          <h2 className="eyebrow mb-3">{g.title}</h2>
          <ul className="space-y-3">
            {g.items.map(({ page, body }) => (
              <li key={page.index} className="rounded-xl border border-border bg-surface p-4">
                <button onClick={() => onOpen(g.chapterIdx, page.index)} className="mb-2 text-left text-[14.5px] font-medium tracking-tight text-accent-text hover:underline">
                  {page.title}
                </button>
                <Markdown compact>{body}</Markdown>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
