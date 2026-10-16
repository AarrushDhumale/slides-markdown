import { LIMITS } from "@/lib/limits";
import { admin, body, countSince, HttpError, requireNotebook, requireUser, route } from "@/lib/server";

// POST { notebook_id, filename, size } -> { path, token }: a signed upload URL for one PDF, after the
// size and quota checks. The browser uploads straight to Storage (no 4.5 MB Vercel body limit), then
// registers the file with /api/sources.
export const POST = route(async (req) => {
  const user = await requireUser(req);
  const { notebook_id, filename, size } = await body<{ notebook_id: string; filename: string; size: number }>(req);
  await requireNotebook(user.id, notebook_id);
  if (typeof filename !== "string" || !/\.pdf$/i.test(filename)) throw new HttpError(400, "Only PDF files are supported");
  if (typeof size !== "number" || size <= 0) throw new HttpError(400, "Empty file");
  if (size > LIMITS.maxPdfBytes) throw new HttpError(413, `PDFs must be under ${LIMITS.maxPdfBytes / 1048576} MB`);
  const { count } = await admin()
    .from("sources")
    .select("*", { count: "exact", head: true })
    .eq("notebook_id", notebook_id);
  if ((count ?? 0) >= LIMITS.maxPdfsPerNotebook) {
    throw new HttpError(429, `A notebook holds at most ${LIMITS.maxPdfsPerNotebook} PDFs`);
  }
  if ((await countSince("sources", "owner", user.id, 24)) >= LIMITS.uploadsPerDay) {
    throw new HttpError(429, "Daily upload limit reached, try again tomorrow");
  }
  const path = `${notebook_id}/${crypto.randomUUID()}.pdf`;
  const { data, error } = await admin().storage.from("pdfs").createSignedUploadUrl(path);
  if (error) throw new HttpError(500, error.message);
  return Response.json({ path, token: data.token });
});
