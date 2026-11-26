import { defineConfig } from "vite";
import fs from "node:fs";
import path from "node:path";

// base "./": dist/ works from any sub-path. Source-only data (raw downloads, build scripts, the
// old world) is not shipped.
let outDir = "dist";
const dropSourceData = {
  name: "drop-source-data",
  configResolved(c: { root: string; build: { outDir: string } }) { outDir = path.resolve(c.root, c.build.outDir); },
  closeBundle() {
    const d = path.join(outDir, "data");
    for (const f of ["raw", "build_v0.py", "build_v1.py", "build_v1a.py", "dl_posters.py", "sub.py", "world_v0.json"]) fs.rmSync(path.join(d, f), { recursive: true, force: true });
  },
};
export default defineConfig({
  base: "./",
  plugins: [dropSourceData],
  server: { host: "127.0.0.1", port: 5199, strictPort: true, allowedHosts: [".ts.net"] },
});
