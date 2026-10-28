// Server-only helpers for API routes: service-role Supabase client, caller auth, error handling.
import { createClient, type SupabaseClient, type User } from "@supabase/supabase-js";

export class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

let adminClient: SupabaseClient | null = null;

export function admin(): SupabaseClient {
  if (!adminClient) {
    const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
    const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
    if (!url || !key) throw new HttpError(500, "Server is missing Supabase configuration");
    adminClient = createClient(url, key, { auth: { persistSession: false, autoRefreshToken: false } });
  }
  return adminClient;
}

/** The signed-in (possibly anonymous) user behind a request's `Authorization: Bearer <jwt>`. */
export async function requireUser(req: Request): Promise<User> {
  const token = req.headers.get("authorization")?.replace(/^Bearer\s+/i, "");
  if (!token) throw new HttpError(401, "Not signed in");
  const { data, error } = await admin().auth.getUser(token);
  if (error || !data.user) throw new HttpError(401, "Session expired, reload the page");
  return data.user;
}

export type NotebookRow = { id: string; owner: string; title: string; status: string; updated_at: string };

export async function requireNotebook(userId: string, notebookId: unknown): Promise<NotebookRow> {
  if (typeof notebookId !== "string" || !/^[0-9a-f-]{36}$/.test(notebookId)) {
    throw new HttpError(400, "Bad notebook id");
  }
  const { data, error } = await admin().from("notebooks").select("*").eq("id", notebookId).maybeSingle();
  if (error) throw new HttpError(500, error.message);
  if (!data || data.owner !== userId) throw new HttpError(404, "Notebook not found");
  return data as NotebookRow;
}

export type ChapterRow = { id: string; slug: string; title: string };

/** A library chapter that is one of this notebook's PDFs (a user can only touch chapters they have). */
export async function requireChapter(notebookId: string, chapterId: unknown): Promise<ChapterRow> {
  if (typeof chapterId !== "string" || !/^[0-9a-f-]{36}$/.test(chapterId)) throw new HttpError(400, "Bad chapter");
  const db = admin();
  const { data: source } = await db.from("sources").select("id").eq("notebook_id", notebookId)
    .eq("chapter_id", chapterId).limit(1);
  if (!source?.length) throw new HttpError(404, "Chapter not found");
  const { data } = await db.from("chapters").select("id, slug, title").eq("id", chapterId).maybeSingle();
  if (!data) throw new HttpError(404, "Chapter not found");
  return data as ChapterRow;
}

export async function body<T>(req: Request): Promise<T> {
  try {
    return (await req.json()) as T;
  } catch {
    throw new HttpError(400, "Expected a JSON body");
  }
}

/** Count rows matching a filter (for quotas). */
export async function countSince(table: string, column: string, value: string, hours: number): Promise<number> {
  const since = new Date(Date.now() - hours * 3600_000).toISOString();
  const { count, error } = await admin()
    .from(table)
    .select("*", { count: "exact", head: true })
    .eq(column, value)
    .gte("created_at", since);
  if (error) throw new HttpError(500, error.message);
  return count ?? 0;
}

export function route(handler: (req: Request) => Promise<Response>) {
  return async (req: Request) => {
    try {
      return await handler(req);
    } catch (e) {
      if (e instanceof HttpError) return Response.json({ error: e.message }, { status: e.status });
      console.error(e);
      return Response.json({ error: "Something went wrong" }, { status: 500 });
    }
  };
}
