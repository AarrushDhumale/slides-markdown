import { LEVELS, LIMITS, type Level } from "@/lib/limits";
import { missingLevels } from "@/lib/library";
import { admin, body, countSince, HttpError, requireNotebook, requireUser, route } from "@/lib/server";

// POST { notebook_id, levels } -> { id } | { ready: true }: queue a conversion. Chapters (one per PDF) that already
// have every requested level, made for any notebook, are not generated again: when nothing is missing no job is
// queued and no quota is used. The first job makes learner + summary
// (the summary is condensed from the learner doc, and the quiz comes from it); other levels on demand.
export const POST = route(async (req) => {
  const user = await requireUser(req);
  const { notebook_id, levels } = await body<{ notebook_id: string; levels: Level[] }>(req);
  await requireNotebook(user.id, notebook_id);
  const wanted = [...new Set(Array.isArray(levels) ? levels : [])].filter((l): l is Level =>
    (LEVELS as readonly string[]).includes(l),
  );
  if (!wanted.length) throw new HttpError(400, "Choose what to generate");
  if ((wanted.includes("summary") || wanted.includes("notes")) && !wanted.includes("learner")) wanted.push("learner");

  const db = admin();
  const { data: sources } = await db.from("sources").select("chapter_id").eq("notebook_id", notebook_id);
  if (!sources?.length) throw new HttpError(400, "Add a PDF first");
  // a source the worker hasn't registered yet (no chapter_id) always needs a job
  const chapterIds = [...new Set(sources.map((s) => s.chapter_id).filter((id): id is string => !!id))];
  const unregistered = sources.some((s) => !s.chapter_id);
  const { data: docs } = chapterIds.length
    ? await db.from("chapter_docs").select("chapter_id, level").in("chapter_id", chapterIds)
    : { data: [] };
  const { data: known } = chapterIds.length ? await db.from("chapters").select("pages").in("id", chapterIds) : { data: [] };
  const missing = chapterIds.some((id) =>
    missingLevels((docs ?? []).filter((d) => d.chapter_id === id).map((d) => d.level), wanted).length > 0,
  );
  if (!unregistered && !missing) {
    // everything exists already (made for someone else): no job will run, so mark the notebook ready here
    await db.from("notebooks")
      .update({ status: "ready", slide_count: (known ?? []).reduce((n, c) => n + (c.pages ?? 0), 0), updated_at: new Date().toISOString() })
      .eq("id", notebook_id).in("status", ["empty", "failed"]);
    return Response.json({ ready: true });
  }

  // an active job already making these levels: reuse it instead of paying twice
  const { data: active } = await db
    .from("jobs")
    .select("id, levels")
    .eq("notebook_id", notebook_id)
    .in("status", ["queued", "running"]);
  const covering = active?.find((j) => wanted.every((l) => (j.levels as string[]).includes(l)));
  if (covering) return Response.json({ id: covering.id, reused: true });

  if ((await countSince("jobs", "owner", user.id, 24)) >= LIMITS.jobsPerDay) {
    throw new HttpError(429, "Daily generation limit reached, try again tomorrow");
  }
  const { count: queued } = await db
    .from("jobs")
    .select("*", { count: "exact", head: true })
    .eq("status", "queued");
  if ((queued ?? 0) >= LIMITS.globalQueue) throw new HttpError(503, "The queue is full right now, try again soon");

  const { data, error } = await db
    .from("jobs")
    .insert({ notebook_id, owner: user.id, levels: wanted })
    .select("id")
    .single();
  if (error) throw new HttpError(500, error.message);
  await db.from("notebooks").update({ status: "queued", updated_at: new Date().toISOString() })
    .eq("id", notebook_id).in("status", ["empty", "ready", "failed"]);
  return Response.json({ id: data.id });
});
