import { admin, body, HttpError, requireUser, route } from "@/lib/server";

// POST { title } -> { id }: a new, empty notebook owned by the caller.
export const POST = route(async (req) => {
  const user = await requireUser(req);
  const { title } = await body<{ title?: string }>(req);
  const clean = (title ?? "").replace(/\s+/g, " ").trim().slice(0, 120) || "Untitled notebook";
  const { data, error } = await admin()
    .from("notebooks")
    .insert({ owner: user.id, title: clean })
    .select("id")
    .single();
  if (error) throw new HttpError(500, error.message);
  return Response.json({ id: data.id });
});
