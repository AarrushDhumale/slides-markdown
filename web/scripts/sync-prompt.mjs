// Copies backend prompts into generated TypeScript files so the Vercel routes use the exact prompts the Python
// worker uses: explain.md -> lib/explain-prompt.generated.ts, and the writing prompts (common + one per level)
// -> lib/write-prompts.generated.ts (per-section rewrites). The generated files are committed, so a Vercel
// build without the repo root still works.
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const prompts = join(here, "..", "..", "backend", "prompts");
const lib = join(here, "..", "lib");

function write(out, body) {
  if (!existsSync(out) || readFileSync(out, "utf8") !== body) writeFileSync(out, body);
}

const explain = join(prompts, "explain.md");
if (existsSync(explain)) {
  write(
    join(lib, "explain-prompt.generated.ts"),
    `// Generated from backend/prompts/explain.md by scripts/sync-prompt.mjs; do not edit.\nexport const EXPLAIN_PROMPT = ${JSON.stringify(readFileSync(explain, "utf8"))};\n`,
  );
}

const names = ["common", "learner", "proficient", "donkey"];
if (names.every((n) => existsSync(join(prompts, `${n}.md`)))) {
  const entries = names.map((n) => `  ${n}: ${JSON.stringify(readFileSync(join(prompts, `${n}.md`), "utf8"))},`);
  write(
    join(lib, "write-prompts.generated.ts"),
    `// Generated from backend/prompts/{common,learner,proficient,donkey}.md by scripts/sync-prompt.mjs; do not edit.\nexport const WRITE_PROMPTS = {\n${entries.join("\n")}\n} as const;\n`,
  );
}
