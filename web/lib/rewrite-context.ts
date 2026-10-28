// Per-section rewrite ("Enhance"): one section of a chapter written again at another level. The system prompt is
// the Python writer's (backend/prompts common.md + <level>.md, copied by scripts/sync-prompt.mjs) and the user
// message mirrors backend/pipeline/write.py:section_input, so a rewrite reads like the generated docs. The
// context is built here from the slide extracts and the chapter's own doc, never from client-supplied text.
import { REWRITE_LEVELS, type RewriteLevel } from "./limits.ts";
import { splitDoc } from "./slide-anchors.ts";
import { WRITE_PROMPTS } from "./write-prompts.generated.ts";

// Same numbers as backend/pipeline/write.py (target words per word of slide content, clamped).
export const LENGTH_FACTOR: Record<RewriteLevel, number> = { proficient: 0.6, learner: 1.1, donkey: 1.3 };
export const MIN_WORDS = 120;
export const MAX_WORDS = 900;
const MAX_SLIDE_JSON_CHARS = 14000;

type Extract = {
  title?: string;
  bullets?: string[];
  tables?: string[];
  equations?: string[];
  code?: { code: string }[];
  figures?: { description?: string; importance?: string }[];
} & Record<string, unknown>;
export type Extracts = Record<string, Extract>;

export class RewriteError extends Error {}

export const isRewriteLevel = (l: unknown): l is RewriteLevel => (REWRITE_LEVELS as readonly unknown[]).includes(l);

const words = (t: string) => t.split(/\s+/).filter(Boolean).length;

/** Port of write.py:source_words. */
export function sourceWords(ids: string[], extracts: Extracts): number {
  let n = 0;
  for (const sid of ids) {
    const ex = extracts[sid];
    if (!ex) continue;
    n += [ex.title ?? "", ...(ex.bullets ?? []), ...(ex.tables ?? [])].reduce((m, t) => m + words(t), 0);
    n += 6 * (ex.equations?.length ?? 0) + (ex.code ?? []).reduce((m, c) => m + 3 * c.code.split("\n").length, 0);
    n += (ex.figures ?? []).filter((f) => f.importance !== "decorative").reduce((m, f) => m + Math.floor(words(f.description ?? "") / 4), 0);
  }
  return n;
}

export function targetWords(level: RewriteLevel, ids: string[], extracts: Extracts): number {
  const raw = LENGTH_FACTOR[level] * sourceWords(ids, extracts);
  return Math.max(MIN_WORDS, Math.min(MAX_WORDS, Math.round(raw / 10) * 10));
}

export function systemPrompt(level: RewriteLevel): string {
  return `${WRITE_PROMPTS.common}\n\n${WRITE_PROMPTS[level]}`;
}

/** `![alt](assets/x.png)` lines of the current section: the figures the rewrite may embed. */
export function figureLines(md: string): string[] {
  return [...md.matchAll(/!\[([^\]]*)\]\((assets\/[\w.-]+\.(?:png|webp|jpg))\)/g)].map(
    (m) => `- ${m[2]}${m[1] ? `: ${m[1]}` : ""}`,
  );
}

export type RewriteMessages = { messages: { role: "system" | "user"; content: string }[]; section: number; budget: number };

/**
 * @param doc    the chapter's learner doc (its sections and slide anchors define the section index)
 * @param level  the level to write
 */
export function buildRewriteMessages(doc: string, extracts: Extracts, section: number, level: RewriteLevel): RewriteMessages {
  const { preamble, sections } = splitDoc(doc);
  const sec = sections[section];
  if (!sec) throw new RewriteError(`Section ${section} not found`);
  const chapterTitle = preamble.match(/^# (.+)$/m)?.[1].trim() ?? "Notes";
  const heading = (md: string, i: number) => md.match(/^#{1,6}[ \t]+(.+?)[ \t]*$/m)?.[1].replace(/\*+/g, "").trim() || `Topic ${i + 1}`;
  const title = heading(sec.md, section);
  const toc = sections.map((s, i) => `${i === section ? "->" : "  "} ${i + 1}. ${heading(s.md, i)}`).join("\n");

  const blocks = sec.slides
    .filter((sid) => extracts[sid])
    .map((sid) => {
      const { slide_role: _role, figures: _figures, ...rest } = extracts[sid];
      const kept = Object.fromEntries(
        Object.entries(rest).filter(([, v]) => v !== null && v !== "" && !(Array.isArray(v) && !v.length)),
      );
      return `### Slide ${sid}\n${JSON.stringify(kept)}`;
    });
  if (!blocks.length) throw new RewriteError("This section has no slide text to rewrite from");
  const figs = figureLines(sec.md);
  const budget = targetWords(level, sec.slides, extracts);
  const user =
    `Chapter: ${chapterTitle}\n\nChapter outline (you are writing the section marked ->):\n${toc}\n\n` +
    `Section to write: ${title}\n` +
    `Heading to use: ## ${title}\n` +
    `Target length: at most ${budget} words. Spend them on explaining the source content, not on outside background.\n` +
    (figs.length ? `\nFigures you can embed (use these exact paths):\n${figs.join("\n")}\n` : "") +
    `\nSource slides:\n\n${blocks.join("\n\n").slice(0, MAX_SLIDE_JSON_CHARS)}`;
  return { messages: [{ role: "system", content: systemPrompt(level) }, { role: "user", content: user }], section, budget };
}

/** Tidy a model reply into a section body: no code fence around it, no slide anchors, always one `##` heading. */
export function cleanSection(raw: string, fallbackTitle: string): string {
  let t = raw.trim().replace(/^```(?:markdown|md)?\s*\n([\s\S]*?)\n```$/i, "$1").replace(/<!-- slides [^>]*-->\n?/g, "").trim();
  if (!/^#{1,6}\s/.test(t)) t = `## ${fallbackTitle}\n\n${t}`;
  return t.replace(/^#{1,6}(\s)/, "##$1");
}

export function sectionTitle(doc: string, section: number): string {
  const sec = splitDoc(doc).sections[section];
  return sec?.md.match(/^#{1,6}[ \t]+(.+?)[ \t]*$/m)?.[1].replace(/\*+/g, "").trim() ?? `Topic ${section + 1}`;
}
