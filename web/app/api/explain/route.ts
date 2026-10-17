import { createHash } from "node:crypto";

import { buildMessages, ExplainError, validateInput, type ExplainInput, type Message } from "@/lib/explain-context";
import { LEVELS, LIMITS } from "@/lib/limits";
import { admin, body, countSince, HttpError, requireNotebook, requireUser, route } from "@/lib/server";

export const maxDuration = 60;

type Req = ExplainInput & { notebook_id: string; level: string; force_cloud?: boolean; request_id?: string };

/**
 * POST -> one of
 *   { answer, source, cached: true }        same question answered before
 *   { request_id }                          queued for the local model; watch the row via Realtime
 *   text/plain stream (X-Explain-Source: cloud)   answered by Gemini when the worker is offline/slow
 */
export const POST = route(async (req) => {
  const user = await requireUser(req);
  const input = await body<Req>(req);
  await requireNotebook(user.id, input.notebook_id);
  if (!(LEVELS as readonly string[]).includes(input.level)) throw new HttpError(400, "Bad level");
  try {
    validateInput(input);
  } catch (e) {
    if (e instanceof ExplainError) throw new HttpError(400, e.message);
    throw e;
  }
  const db = admin();
  const nb = input.notebook_id;

  const { data: doc } = await db
    .from("documents")
    .select("storage_path, updated_at")
    .eq("notebook_id", nb)
    .eq("level", input.level)
    .maybeSingle();
  if (!doc) throw new HttpError(404, "Generate these notes first");

  const cacheKey = createHash("sha256")
    .update(JSON.stringify([nb, input.level, doc.updated_at, input.section, input.selection ?? null,
      input.image ?? null, input.history ?? [], Boolean(input.simpler)]))
    .digest("hex")
    .slice(0, 32);
  const { data: hit } = await db.from("explain_cache").select("answer, source").eq("key", cacheKey).maybeSingle();
  if (hit) return Response.json({ answer: hit.answer, source: hit.source, cached: true });

  if (input.request_id) {
    // the local worker didn't pick it up in time: retire the queued row, answer from the cloud instead
    await db.from("explain_requests").update({ status: "failed", error: "answered by cloud fallback" })
      .eq("id", input.request_id).eq("owner", user.id).eq("status", "queued");
  } else if ((await countSince("explain_requests", "owner", user.id, 1)) >= LIMITS.explainsPerHour) {
    throw new HttpError(429, "That's a lot of questions! Try again in a few minutes.");
  }

  if (!input.force_cloud) {
    const { data: workers } = await db.from("worker_status").select("last_seen, local_model")
      .order("last_seen", { ascending: false }).limit(1);
    const w = workers?.[0];
    if (w && w.local_model && Date.now() - new Date(w.last_seen).getTime() < LIMITS.workerFreshMs) {
      const { data, error } = await db.from("explain_requests").insert({
        notebook_id: nb, owner: user.id, level: input.level, section: input.section,
        selection: input.selection ?? null, image: input.image ?? null, history: input.history ?? [],
        simpler: Boolean(input.simpler), cache_key: cacheKey,
      }).select("id").single();
      if (error) throw new HttpError(500, error.message);
      return Response.json({ request_id: data.id });
    }
  }
  return cloudAnswer(user.id, input, doc.storage_path, cacheKey);
});

async function download(path: string): Promise<Blob> {
  const { data, error } = await admin().storage.from("outputs").download(path);
  if (error || !data) throw new HttpError(404, `Missing file ${path.split("/").slice(1).join("/")}`);
  return data;
}

async function imageDataUrl(nb: string, image: string | null | undefined): Promise<string | null> {
  if (!image) return null;
  let rel = image;
  if (image.startsWith("slide:")) {
    const index = JSON.parse(await (await download(`${nb}/slides.json`)).text()) as Record<string, { image: string }>;
    const entry = index[image.slice(6)];
    if (!entry) throw new HttpError(404, "Slide not found");
    rel = entry.image;
  }
  const blob = await download(`${nb}/${rel}`);
  const b64 = Buffer.from(await blob.arrayBuffer()).toString("base64");
  return `data:${blob.type || (rel.endsWith(".webp") ? "image/webp" : "image/png")};base64,${b64}`;
}

async function cloudAnswer(owner: string, input: Req, docPath: string, cacheKey: string): Promise<Response> {
  const key = process.env.GEMINI_API_KEY;
  if (!key) throw new HttpError(503, "Explanations are offline right now (no local worker, no cloud key)");
  const model = process.env.GEMINI_EXPLAIN_MODEL || "gemini-3.1-flash-lite";
  const nb = input.notebook_id;
  const [doc, extracts, image] = await Promise.all([
    download(docPath).then((b) => b.text()),
    download(`${nb}/extracts.json`).then(async (b) => JSON.parse(await b.text())),
    imageDataUrl(nb, input.image),
  ]);
  let messages: Message[];
  try {
    messages = buildMessages(doc, extracts, input, image);
  } catch (e) {
    if (e instanceof ExplainError) throw new HttpError(400, e.message);
    throw e;
  }
  const upstream = await fetch("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", {
    method: "POST",
    headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify({ model, messages, stream: true, max_tokens: 450, temperature: 0.3, reasoning_effort: "none" }),
  });
  if (!upstream.ok || !upstream.body) {
    console.error("gemini", upstream.status, await upstream.text().catch(() => ""));
    throw new HttpError(502, "The cloud model is unavailable, try again shortly");
  }

  const decoder = new TextDecoder();
  const encoder = new TextEncoder();
  let answer = "";
  const reader = upstream.body.getReader();
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      let buffer = "";
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";
          for (const line of lines) {
            const data = line.replace(/^data:\s*/, "").trim();
            if (!data || data === "[DONE]" || !line.startsWith("data:")) continue;
            try {
              const delta = JSON.parse(data).choices?.[0]?.delta?.content;
              if (delta) {
                answer += delta;
                controller.enqueue(encoder.encode(delta));
              }
            } catch {
              /* keep-alive or partial line */
            }
          }
        }
        controller.close();
      } catch (e) {
        controller.error(e);
        return;
      }
      if (answer.trim()) {
        const db = admin();
        await Promise.all([
          db.from("explain_cache").upsert({ key: cacheKey, answer, model, source: "cloud" }),
          db.from("explain_requests").insert({
            notebook_id: nb, owner, level: input.level, section: input.section, selection: input.selection ?? null,
            image: input.image ?? null, history: input.history ?? [], simpler: Boolean(input.simpler),
            cache_key: cacheKey, status: "done", answer, partial_text: answer, model, source: "cloud",
          }),
        ]);
      }
    },
  });
  return new Response(stream, {
    headers: { "Content-Type": "text/plain; charset=utf-8", "X-Explain-Source": "cloud", "Cache-Control": "no-store" },
  });
}
