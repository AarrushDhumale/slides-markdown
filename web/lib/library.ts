// The shared chapter library: a chapter is one PDF, identified by its content hash. Pure helpers shared by
// the API routes and tests; they mirror backend/worker.py (missing_levels, ensure_chapter).

/** Levels still to generate for a chapter. The summary and notes are condensed from the learner doc, so a
 * missing learner doc is generated along with them (the quiz comes from it too). */
export function missingLevels(have: Iterable<string>, wanted: readonly string[]): string[] {
  const got = new Set(have);
  const need = wanted.filter((l) => !got.has(l) && l !== "quiz");
  if (need.some((l) => l === "summary" || l === "notes") && !got.has("learner") && !need.includes("learner")) {
    need.push("learner");
  }
  return need;
}

/** URL-safe, human-readable chapter name: `demand-paging-3f9a1c2b` (title words + first 8 hash chars). */
export function chapterSlug(filename: string, sha: string): string {
  const stem = filename.replace(/\.pdf$/i, "");
  const base = stem
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60)
    .replace(/-+$/g, "");
  return base ? `${base}-${sha.slice(0, 8)}` : sha.slice(0, 8);
}

/** Key of a section across chapters (per-section choices, variants and notes). */
export const sectionKey = (chapterId: string, section: number) => `${chapterId}:${section}`;
