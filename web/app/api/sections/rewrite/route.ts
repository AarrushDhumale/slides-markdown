import { isRewriteLevel, buildRewriteMessages, cleanSection, RewriteError, sectionTitle } from "@/lib/rewrite-context";
import { LIMITS } from "@/lib/limits";
import { admin, body, countSince, HttpError, requireChapter, requireNotebook, requireUser, route } from "@/lib/server";
import { splitDoc } from "@/lib/slide-anchors";

export const maxDuration = 60;

const MODEL = process.env.REWRITE_MODEL || "deepseek/deepseek-v3.2";

type Req = { notebook_id: string; chapter_id: string; section: number; level: string | null };

/**
 * POST { notebook_id, chapter_id, section, level } -> { level, body, cached }
 * Show one section at another level (learner | proficient | donkey) in this notebook; other sections are untouched.
 * `level: null` goes back to the document's own text. The rewrite is shared: any notebook with the same chapter
 * gets it for free. Resolution order: an existing rewrite -> the same section of that level's full doc (no model
 * call) -> a new OpenRouter call (DeepSeek), which is quota-limited.
 */
export const POST = route(async (req) => {
  const user = await requireUser(req);
  const input = await body<Req>(req);
  await requireNotebook(user.id, input.notebook_id);
  const chapter = await requireChapter(input.notebook_id, input.chapter_id);
  if (!Number.isInteger(input.section) || input.section < 0 || input.section > 500) throw new HttpError(400, "Bad section");
  const db = admin();
  const where = { notebook_id: input.notebook_id, chapter_id: chapter.id, section: input.section };

  if (input.level === null) {
    await db.from("section_choices").delete().match(where);
    return Response.json({ level: null });
  }
  if (!isRewriteLevel(input.level)) throw new HttpError(400, "Pick learner, revision or simple");
  const level = input.level;
  const choose = async (text: string, cached: boolean) => {
    const { error } = await db.from("section_choices")
      .upsert({ ...where, level, updated_at: new Date().toISOString() }, { onConflict: "notebook_id,chapter_id,section" });
    if (error) throw new HttpError(500, error.message);
    return Response.json({ level, body: text, cached });
  };

  const { data: hit } = await db.from("section_variants").select("body").eq("chapter_id", chapter.id)
    .eq("section", input.section).eq("level", level).maybeSingle();
  if (hit) return choose(hit.body, true);

  // the chapter already has a full doc at that level: take its section, no model call
  const { data: levelDoc } = await db.from("chapter_docs").select("level").eq("chapter_id", chapter.id).eq("level", level).maybeSingle();
  if (levelDoc) {
    const doc = await download(`${chapter.slug}/${level}.md`).then((b) => b.text());
    const section = splitDoc(doc).sections[input.section];
    if (!section) throw new HttpError(404, "Section not found");
    await db.from("section_variants").upsert({ chapter_id: chapter.id, section: input.section, level, body: section.md, model: null, created_by: user.id });
    return choose(section.md, true);
  }

  const key = process.env.OPENROUTER_API_KEY;
  if (!key) throw new HttpError(503, "Rewriting is unavailable right now");
  if ((await countSince("section_variants", "created_by", user.id, 1)) >= LIMITS.rewritesPerHour) {
    throw new HttpError(429, "That's a lot of rewrites! Try again in a few minutes.");
  }
  const [learner, extracts] = await Promise.all([
    download(`${chapter.slug}/learner.md`).then((b) => b.text()).catch(() => {
      throw new HttpError(404, "Generate the learner notes first");
    }),
    download(`${chapter.slug}/extracts.json`).then(async (b) => JSON.parse(await b.text())),
  ]);
  let built;
  try {
    built = buildRewriteMessages(learner, extracts, input.section, level);
  } catch (e) {
    if (e instanceof RewriteError) throw new HttpError(400, e.message);
    throw e;
  }
  const upstream = await fetch("https://openrouter.ai/api/v1/chat/completions", {
    method: "POST",
    headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    signal: AbortSignal.timeout(50_000),
    body: JSON.stringify({
      model: MODEL,
      messages: built.messages,
      temperature: 0.4,
      max_tokens: Math.min(4000, Math.round(built.budget * 2.2) + 400),
      // same hosts/options as the pipeline's DeepSeek backend (backend/llm/backends.yaml `deepseek_v32`)
      reasoning: { enabled: false },
      usage: { include: true },
      provider: { order: ["AtlasCloud", "Baidu"], quantizations: ["fp8", "bf16", "fp16"] },
    }),
  }).catch(() => null);
  if (!upstream?.ok) {
    console.error("openrouter", upstream?.status, await upstream?.text().catch(() => ""));
    throw new HttpError(502, "The writing model is unavailable, try again shortly");
  }
  const json = await upstream.json();
  const raw: string = json.choices?.[0]?.message?.content ?? "";
  if (raw.trim().split(/\s+/).length < 20) throw new HttpError(502, "The model returned too little, try again");
  const text = cleanSection(raw, sectionTitle(learner, input.section));
  await db.from("section_variants").upsert({
    chapter_id: chapter.id, section: input.section, level, body: text, model: MODEL,
    cost_usd: Number(json.usage?.cost ?? 0), created_by: user.id,
  });
  return choose(text, false);
});

async function download(path: string): Promise<Blob> {
  const { data, error } = await admin().storage.from("library").download(path);
  if (error || !data) throw new HttpError(404, `Missing file ${path.split("/").slice(1).join("/")}`);
  return data;
}
