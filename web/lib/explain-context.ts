// Cloud fallback for "Explain this": the same context the Python worker builds
// (backend/pipeline/explain.py), so local and cloud answers are grounded identically.
import { EXPLAIN_PROMPT } from "./explain-prompt.generated.ts";
import { splitDoc } from "./slide-anchors.ts";

export const IMAGE_REF_RE = /^(assets\/[\w.-]+\.png|slide:[A-Z]+·\d+)$/;
const MAX_SECTION_CHARS = 6000;
const MAX_SELECTION_CHARS = 1500;
export const MAX_TURNS = 3;

export type Turn = { role: "user" | "assistant"; content: string };
export type ExplainInput = {
  section: number;
  selection?: string | null;
  image?: string | null;
  history?: Turn[];
  simpler?: boolean;
};
type Part = { type: "text"; text: string } | { type: "image_url"; image_url: { url: string } };
export type Message = { role: "system" | "user" | "assistant"; content: string | Part[] };

export class ExplainError extends Error {}

export function validateInput(input: ExplainInput): void {
  if (!Number.isInteger(input.section) || input.section < 0) throw new ExplainError("Bad section");
  if (!input.selection?.trim() && !input.image) throw new ExplainError("Nothing selected");
  if (input.image && !IMAGE_REF_RE.test(input.image)) throw new ExplainError("Bad image reference");
  const turns = (input.history ?? []).filter((t) => t.role === "user" || t.role === "assistant");
  if (turns.filter((t) => t.role === "user").length > MAX_TURNS) {
    throw new ExplainError(`At most ${MAX_TURNS} follow-up questions per explanation`);
  }
}

function slidesJson(ids: string[], extracts: Record<string, Record<string, unknown>>): string {
  const rows = ids
    .filter((sid) => extracts[sid])
    .map((sid) => {
      const { slide_role: _role, ...rest } = extracts[sid];
      const kept = Object.fromEntries(
        Object.entries(rest).filter(([, v]) => v !== null && v !== "" && !(Array.isArray(v) && !v.length)),
      );
      return { slide: sid, ...kept };
    });
  return JSON.stringify(rows).slice(0, 12000);
}

export function buildMessages(
  doc: string,
  extracts: Record<string, Record<string, unknown>>,
  input: ExplainInput,
  imageDataUrl: string | null,
): Message[] {
  validateInput(input);
  const { sections } = splitDoc(doc);
  const sec = sections[input.section];
  if (!sec) throw new ExplainError(`Section ${input.section} not found`);
  let slideIds = sec.slides;
  if (input.image?.startsWith("slide:") && !slideIds.includes(input.image.slice(6))) {
    slideIds = [...slideIds, input.image.slice(6)];
  }
  const selection = (input.selection ?? "").trim().slice(0, MAX_SELECTION_CHARS);
  const history = (input.history ?? []).filter((t) => t.role === "user" || t.role === "assistant");
  let ask = selection
    ? `SELECTION (highlighted text):\n<<<\n${selection}\n>>>`
    : `SELECTION: the attached figure (${input.image}).`;
  if (input.simpler && !history.length) ask += "\n\nExplain it as simply as you can.";
  const text = `SLIDES:\n${slidesJson(slideIds, extracts)}\n\nNOTES:\n<<<\n${sec.md.slice(0, MAX_SECTION_CHARS)}\n>>>\n\n${ask}`;
  const content: string | Part[] = imageDataUrl
    ? [{ type: "text", text }, { type: "image_url", image_url: { url: imageDataUrl } }]
    : text;
  const messages: Message[] = [
    { role: "system", content: EXPLAIN_PROMPT },
    { role: "user", content },
    ...history.map((t) => ({ role: t.role, content: String(t.content).slice(0, 2000) })),
  ];
  if (history.length && input.simpler) messages.push({ role: "user", content: "Explain that more simply." });
  if (messages[messages.length - 1].role !== "user") {
    throw new ExplainError("A follow-up must end with your question");
  }
  return messages;
}
