import { LIMITS } from "@/lib/limits";
import { admin, body, HttpError, requireNotebook, requireUser, route } from "@/lib/server";

// POST { notebook_id, path, filename } -> { id }: record an uploaded PDF (after checking it really landed).
export const POST = route(async (req) => {
  const user = await requireUser(req);
  const { notebook_id, path, filename } = await body<{ notebook_id: string; path: string; filename: string }>(req);
  await requireNotebook(user.id, notebook_id);
  const name = typeof path === "string" ? path.split("/").pop() ?? "" : "";
  if (!path?.startsWith(`${notebook_id}/`) || !/^[0-9a-f-]{36}\.pdf$/.test(name)) {
    throw new HttpError(400, "Bad upload path");
  }
  const { data: listing, error: listError } = await admin()
    .storage.from("pdfs")
    .list(notebook_id, { search: name, limit: 1 });
  if (listError) throw new HttpError(500, listError.message);
  const object = listing?.find((o) => o.name === name);
  if (!object) throw new HttpError(400, "Upload not found, try again");
  const size = Number(object.metadata?.size ?? 0);
  if (size > LIMITS.maxPdfBytes) {
    await admin().storage.from("pdfs").remove([path]);
    throw new HttpError(413, "PDF too large");
  }
  const { count } = await admin()
    .from("sources")
    .select("*", { count: "exact", head: true })
    .eq("notebook_id", notebook_id);
  const { data, error } = await admin()
    .from("sources")
    .insert({
      notebook_id,
      owner: user.id,
      filename: String(filename ?? "slides.pdf").slice(0, 200),
      storage_path: path,
      size_bytes: size,
      position: count ?? 0,
    })
    .select("id")
    .single();
  if (error) throw new HttpError(500, error.message);
  return Response.json({ id: data.id });
});
