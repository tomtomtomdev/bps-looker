// Fails when src/lib/api/schema.d.ts is out of date vs openapi.json (run `pnpm gen:api`).
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const committed = join(root, "src/lib/api/schema.d.ts");
const dir = mkdtempSync(join(tmpdir(), "bps-web-schema-"));
try {
  const fresh = join(dir, "schema.d.ts");
  execFileSync(
    join(root, "node_modules/.bin/openapi-typescript"),
    [join(root, "openapi.json"), "-o", fresh],
    { stdio: ["ignore", "ignore", "inherit"] },
  );
  if (readFileSync(fresh, "utf8") !== readFileSync(committed, "utf8")) {
    console.error(
      "src/lib/api/schema.d.ts is out of date with openapi.json — run `pnpm gen:api` and commit it.",
    );
    process.exit(1);
  }
  console.log("API client schema is up to date.");
} finally {
  rmSync(dir, { recursive: true, force: true });
}
