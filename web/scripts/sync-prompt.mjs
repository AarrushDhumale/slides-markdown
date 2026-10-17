// Copies backend/prompts/explain.md into lib/explain-prompt.generated.ts so the cloud fallback uses the
// exact prompt the Python worker uses. The generated file is committed, so a Vercel build without the
// repo root still works.
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "..", "backend", "prompts", "explain.md");
const out = join(here, "..", "lib", "explain-prompt.generated.ts");
if (existsSync(src)) {
  const body = `// Generated from backend/prompts/explain.md by scripts/sync-prompt.mjs; do not edit.\nexport const EXPLAIN_PROMPT = ${JSON.stringify(readFileSync(src, "utf8"))};\n`;
  if (!existsSync(out) || readFileSync(out, "utf8") !== body) writeFileSync(out, body);
}
