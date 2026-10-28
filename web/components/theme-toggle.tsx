"use client";

import * as Popover from "@radix-ui/react-popover";
import { Check, Palette } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

export const PALETTES = [
  { id: "docs", name: "Docs", font: "Inter", dark: true, swatch: ["#121212", "#e0e0e0", "#38bdf8"] },
  { id: "paper", name: "Paper (light)", font: "Inter", dark: false, swatch: ["#fafaf9", "#1f1f1f", "#0284c7"] },
] as const;

export const FONTS = [
  { id: "inter", name: "Inter", note: "Neutral, like Vercel and Stripe docs", stack: "var(--font-inter)" },
  { id: "atkinson", name: "Atkinson Hyperlegible", note: "Made for legibility; distinct letter shapes", stack: "var(--font-atkinson)" },
  { id: "lora", name: "Lora", note: "Warm serif, book-like prose", stack: "var(--font-lora)" },
  { id: "literata", name: "Literata", note: "Serif designed for long reading on screens", stack: "var(--font-literata)" },
  { id: "nunito", name: "Nunito Sans", note: "Soft, rounded and friendly", stack: "var(--font-nunito)" },
] as const;

export function ThemeToggle() {
  const [current, setCurrent] = useState<string>("docs");
  const [font, setFont] = useState<string>("inter");
  useEffect(() => {
    setCurrent(document.documentElement.dataset.theme ?? "docs");
    setFont(document.documentElement.dataset.font ?? "inter");
  }, []);
  const pickFont = (id: string) => {
    document.documentElement.dataset.font = id;
    setFont(id);
    try {
      localStorage.setItem("font", id);
    } catch {
      /* private mode: the font just won't persist */
    }
  };

  const pick = (id: string, dark: boolean) => {
    document.documentElement.dataset.theme = id;
    document.documentElement.classList.toggle("dark", dark);
    setCurrent(id);
    try {
      localStorage.setItem("palette", id);
    } catch {
      /* private mode: the palette just won't persist */
    }
  };

  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <Button variant="ghost" size="icon" aria-label="Reading theme" title="Reading theme">
          <Palette className="size-[18px]" />
        </Button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align="end" sideOffset={8} className="z-50 w-72 rounded-xl border border-border bg-surface p-1.5 shadow-pop">
          <p className="eyebrow px-2 pt-1.5 pb-1">Reading theme</p>
          {PALETTES.map((p) => (
            <button
              key={p.id}
              onClick={() => pick(p.id, p.dark)}
              className={cn("flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left hover:bg-surface-2", current === p.id && "bg-surface-2")}
            >
              <span className="flex overflow-hidden rounded-md border border-border-strong">
                {p.swatch.map((c) => (
                  <span key={c} className="h-7 w-3.5" style={{ background: c }} />
                ))}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-medium">{p.name}</span>
                <span className="block text-[11.5px] text-faint">{p.font}</span>
              </span>
              {current === p.id && <Check className="size-4 text-accent" />}
            </button>
          ))}
          <p className="eyebrow px-2 pt-3 pb-1">Reading font</p>
          {FONTS.map((f) => (
            <button key={f.id} onClick={() => pickFont(f.id)} className={cn("flex w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left hover:bg-surface-2", font === f.id && "bg-surface-2")}>
              <span className="w-9 shrink-0 text-center text-[22px] leading-none" style={{ fontFamily: f.stack }}>Aa</span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-medium" style={{ fontFamily: f.stack }}>{f.name}</span>
                <span className="block truncate text-[11.5px] text-faint">{f.note}</span>
              </span>
              {font === f.id && <Check className="size-4 text-accent" />}
            </button>
          ))}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
