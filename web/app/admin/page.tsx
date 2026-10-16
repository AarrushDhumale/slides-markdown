"use client";

import { useEffect, useState } from "react";

import { Logo } from "@/components/logo";
import { timeAgo } from "@/components/status";
import { Badge } from "@/components/ui/badge";

type Data = {
  jobs: { id: string; notebook_id: string; levels: string[]; status: string; stage: string | null; done: number; total: number; error: string | null; cost_usd: number; created_at: string }[];
  workers: { id: string; last_seen: string; busy: boolean; local_model: boolean; month_usd: number }[];
  month: { job_usd: number; jobs: number; explains: number };
};

// Dev-only dashboard: /admin?token=ADMIN_TOKEN
export default function Admin() {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const token = new URLSearchParams(location.search).get("token") ?? "";
    const load = () =>
      fetch(`/api/admin?token=${encodeURIComponent(token)}`)
        .then(async (r) => (r.ok ? setData(await r.json()) : setError((await r.json()).error)))
        .catch((e) => setError(String(e)));
    load();
    const t = setInterval(load, 10_000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6">
      <div className="mb-8 flex items-center justify-between"><Logo /><span className="text-sm text-muted">admin</span></div>
      {error && <p className="rounded-xl bg-danger-soft px-4 py-3 text-sm text-danger">{error}</p>}
      {data && (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {[
              ["Spend this month (jobs)", `$${data.month.job_usd.toFixed(3)}`],
              ["Jobs this month", String(data.month.jobs)],
              ["Explanations this month", String(data.month.explains)],
            ].map(([k, v]) => (
              <div key={k} className="rounded-2xl border border-border bg-surface p-4 shadow-card">
                <p className="text-[12.5px] text-muted">{k}</p>
                <p className="mt-1 text-2xl font-semibold tabular-nums">{v}</p>
              </div>
            ))}
          </div>
          <h2 className="mt-8 mb-2 font-semibold">Workers</h2>
          <div className="space-y-2">
            {data.workers.map((w) => (
              <div key={w.id} className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-surface px-4 py-2.5 text-sm">
                <span className="font-medium">{w.id}</span>
                <Badge tone={Date.now() - new Date(w.last_seen).getTime() < 120_000 ? "success" : "danger"}>seen {timeAgo(w.last_seen)}</Badge>
                {w.busy && <Badge tone="accent">busy</Badge>}
                {w.local_model && <Badge>local model ready</Badge>}
                <span className="ml-auto text-muted tabular-nums">ledger ${Number(w.month_usd).toFixed(3)} this month</span>
              </div>
            ))}
          </div>
          <h2 className="mt-8 mb-2 font-semibold">Recent jobs</h2>
          <div className="overflow-x-auto rounded-xl border border-border bg-surface">
            <table className="w-full text-left text-[13px]">
              <thead className="bg-surface-2 text-muted">
                <tr>{["When", "Levels", "Status", "Stage", "Cost", "Error"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
              </thead>
              <tbody>
                {data.jobs.map((j) => (
                  <tr key={j.id} className="border-t border-border align-top">
                    <td className="px-3 py-2 whitespace-nowrap text-muted">{timeAgo(j.created_at)}</td>
                    <td className="px-3 py-2">{j.levels.join(", ")}</td>
                    <td className="px-3 py-2"><Badge tone={j.status === "done" ? "success" : j.status === "failed" ? "danger" : "accent"}>{j.status}</Badge></td>
                    <td className="px-3 py-2 whitespace-nowrap text-muted">{j.stage ?? "–"} {j.total ? `${j.done}/${j.total}` : ""}</td>
                    <td className="px-3 py-2 tabular-nums">${Number(j.cost_usd).toFixed(4)}</td>
                    <td className="max-w-[360px] px-3 py-2 text-danger">{j.error}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
