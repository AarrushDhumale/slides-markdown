import { createHash } from "node:crypto";

import { chapterSlug } from "@/lib/library";
import { LIMITS } from "@/lib/limits";
import { admin, body, HttpError, requireNotebook, requireUser, route } from "@/lib/server";

// POST { notebook_id, path, filename } -> { id, chapter }: record an uploaded PDF (after checking it really
// landed) and register its library chapter. A chapter is identified by the PDF's content hash, computed here from
// the stored bytes (never trusted from the client), so the same PDF uploaded by anyone is the same chapter and
// reuses what was already generated.
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
  const { data: blob, error: downloadError } = await admin().storage.from("pdfs").download(path);
  if (downloadError || !blob) throw new HttpError(500, "Could not read the upload, try again");
  const sha = createHash("sha256").update(Buffer.from(await blob.arrayBuffer())).digest("hex").slice(0, 16);
  const clean = String(filename ?? "slides.pdf").slice(0, 200);
  const db = admin();
  const find = () => db.from("chapters").select("id, slug, title, status, pages").eq("sha", sha).maybeSingle();
  let { data: chapter } = await find();
  if (!chapter) {
    const made = await db
      .from("chapters")
      .insert({ sha, slug: chapterSlug(clean, sha), title: clean.replace(/\.pdf$/i, "").slice(0, 200) || "Slides", filename: clean })
      .select("id, slug, title, status, pages")
      .single();
    chapter = made.data ?? (await find()).data; // someone registered the same PDF a moment ago
  }
  if (!chapter) throw new HttpError(500, "Could not register the chapter");

  const { count } = await admin()
    .from("sources")
    .select("*", { count: "exact", head: true })
    .eq("notebook_id", notebook_id);
  const { data, error } = await admin()
    .from("sources")
    .insert({
      notebook_id,
      owner: user.id,
      filename: clean,
      sha,
      chapter_id: chapter.id,
      pages: chapter.pages ?? null, // known once anyone has converted this PDF
      storage_path: path,
      size_bytes: size,
      position: count ?? 0,
    })
    .select("id")
    .single();
  if (error) throw new HttpError(500, error.message);
  return Response.json({ id: data.id, chapter });
});
