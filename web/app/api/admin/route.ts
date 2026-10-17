import { timingSafeEqual } from "node:crypto";

import { admin, HttpError, route } from "@/lib/server";

// GET ?token=ADMIN_TOKEN -> recent jobs, workers and spend (dev-only dashboard at /admin).
export const GET = route(async (req) => {
  const expected = process.env.ADMIN_TOKEN ?? "";
  const given = new URL(req.url).searchParams.get("token") ?? "";
  if (!expected || given.length !== expected.length || !timingSafeEqual(Buffer.from(given), Buffer.from(expected))) {
    throw new HttpError(403, "Forbidden");
  }
  const db = admin();
  const monthStart = new Date(Date.UTC(new Date().getUTCFullYear(), new Date().getUTCMonth(), 1)).toISOString();
  const [jobs, workers, month, explains] = await Promise.all([
    db.from("jobs").select("id, notebook_id, levels, status, stage, done, total, error, cost_usd, created_at, finished_at")
      .order("created_at", { ascending: false }).limit(50),
    db.from("worker_status").select("*").order("last_seen", { ascending: false }),
    db.from("jobs").select("cost_usd").gte("created_at", monthStart),
    db.from("explain_requests").select("source, status", { count: "exact" }).gte("created_at", monthStart),
  ]);
  const jobSpend = (month.data ?? []).reduce((s, j) => s + Number(j.cost_usd ?? 0), 0);
  return Response.json({
    jobs: jobs.data ?? [],
    workers: workers.data ?? [],
    month: { job_usd: jobSpend, jobs: month.data?.length ?? 0, explains: explains.count ?? 0 },
  });
});
