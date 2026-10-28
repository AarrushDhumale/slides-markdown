"use client";

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let client: SupabaseClient | null = null;

export function supabase(): SupabaseClient {
  if (!client) {
    const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
    const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
    if (!url || !key) throw new Error("NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY are not set");
    client = createClient(url, key, { auth: { persistSession: true, autoRefreshToken: true } });
  }
  return client;
}

let sessionPromise: Promise<string> | null = null;

/** An access token for this browser: signs in anonymously on first visit (no visible login). */
export function ensureSession(): Promise<string> {
  sessionPromise ??= (async () => {
    const { data } = await supabase().auth.getSession();
    if (data.session) return data.session.access_token;
    const { data: anon, error } = await supabase().auth.signInAnonymously();
    if (error || !anon.session) throw new Error(error?.message ?? "Could not start a session");
    return anon.session.access_token;
  })().finally(() => {
    sessionPromise = null;
  });
  return sessionPromise;
}

/** POST to one of our API routes; returns the raw Response (callers handle streams). */
export async function apiRaw(path: string, payload: unknown): Promise<Response> {
  const token = await ensureSession();
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const msg = await res.json().then((j) => j.error).catch(() => null);
    throw new Error(msg ?? `Request failed (${res.status})`);
  }
  return res;
}

export async function api<T>(path: string, payload: unknown): Promise<T> {
  return (await apiRaw(path, payload)).json() as Promise<T>;
}

// ---------- row types (see supabase/migrations/001_init.sql and 003_library.sql) ----------

export type Notebook = {
  id: string;
  title: string;
  status: "empty" | "queued" | "processing" | "ready" | "failed";
  slide_count: number;
  created_at: string;
  updated_at: string;
};
export type Source = {
  id: string;
  filename: string;
  size_bytes: number;
  pages: number | null;
  position: number;
  chapter_id: string | null;
};
export type Job = {
  id: string;
  levels: string[];
  status: "queued" | "running" | "done" | "failed";
  stage: string | null;
  done: number;
  total: number;
  error: string | null;
  cost_usd: number;
  chapter_id: string | null; // the chapter being generated right now
  chapters_done: number;
  chapters_total: number;
  created_at: string;
};
/** A library chapter (one PDF, shared by every notebook containing it). */
export type Chapter = {
  id: string;
  slug: string;
  title: string;
  status: "new" | "generating" | "ready" | "failed";
  error: string | null;
};
export type ChapterDoc = { chapter_id: string; level: string; storage_path: string; word_count: number; updated_at: string };
export type Choice = { chapter_id: string; section: number; level: string };
export type Note = { chapter_id: string; section: number; body: string; updated_at: string };
export type WorkerStatus = { id: string; last_seen: string; busy: boolean; local_model: boolean };

/** Upload PDFs into a notebook (signed URL per file), then register them. */
export async function uploadPdfs(notebookId: string, files: File[], onFile?: (name: string) => void) {
  for (const file of files) {
    onFile?.(file.name);
    const { path, token } = await api<{ path: string; token: string }>("/api/uploads", {
      notebook_id: notebookId,
      filename: file.name,
      size: file.size,
    });
    const { error } = await supabase().storage.from("pdfs").uploadToSignedUrl(path, token, file, {
      contentType: "application/pdf",
    });
    if (error) throw new Error(`${file.name}: ${error.message}`);
    await api("/api/sources", { notebook_id: notebookId, path, filename: file.name });
  }
}
