"use client";

import * as Popover from "@radix-ui/react-popover";
import { Check, ChevronDown, Loader2, RotateCcw, Wand2 } from "lucide-react";

import { cn } from "@/lib/cn";
import { REWRITE_LEVELS, type RewriteLevel } from "@/lib/limits";

export const SECTION_LEVELS: Record<RewriteLevel, { label: string; hint: string }> = {
  learner: { label: "Learner", hint: "Full explanations" },
  proficient: { label: "Revision", hint: "Dense, for revising" },
  donkey: { label: "Explain simply", hint: "Like I'm five" },
};

/** Per-topic level picker: rewrites only this topic at another level; "Default" goes back to the document's text. */
export function SectionLevelMenu({
  level,
  busy,
  disabled,
  onChoose,
}: {
  level?: string;
  busy: boolean;
  disabled?: boolean;
  onChoose: (level: RewriteLevel | null) => void;
}) {
  const label = level && level in SECTION_LEVELS ? SECTION_LEVELS[level as RewriteLevel].label : null;
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button
          disabled={disabled || busy}
          title="Show this topic at another level"
          className={cn(
            "inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 font-sans text-[11.5px] font-medium shadow-card transition disabled:cursor-progress",
            label ? "border-accent/50 bg-accent-soft text-accent-text" : "border-border bg-surface text-muted hover:text-text",
          )}
        >
          {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Wand2 className="size-3.5" />}
          {busy ? "Rewriting…" : (label ?? "Level")}
          <ChevronDown className="size-3" />
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align="end" sideOffset={6} className="rise z-50 w-60 rounded-xl border border-border bg-surface p-1.5 shadow-pop">
          <p className="px-2.5 pt-1.5 pb-1 text-[11.5px] text-faint">Only this topic changes.</p>
          {REWRITE_LEVELS.map((l) => (
            <Popover.Close asChild key={l}>
              <button onClick={() => l !== level && onChoose(l)} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left hover:bg-surface-2">
                <span className="grid size-4 place-items-center">{level === l && <Check className="size-3.5 text-accent" />}</span>
                <span className="min-w-0">
                  <span className="block text-[13px] font-medium">{SECTION_LEVELS[l].label}</span>
                  <span className="block text-[11.5px] text-faint">{SECTION_LEVELS[l].hint}</span>
                </span>
              </button>
            </Popover.Close>
          ))}
          {level && (
            <Popover.Close asChild>
              <button onClick={() => onChoose(null)} className="mt-1 flex w-full items-center gap-2.5 rounded-lg border-t border-border px-2.5 py-2 text-left text-[13px] text-muted hover:bg-surface-2 hover:text-text">
                <RotateCcw className="size-3.5" /> Back to the default
              </button>
            </Popover.Close>
          )}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
