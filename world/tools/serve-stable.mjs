// Static server for stable/ on 127.0.0.1:5198 (no HMR, no rebuilds; reads files on each request).
import http from "node:http"; import fs from "node:fs"; import path from "node:path";
const root = path.resolve(process.env.ROOT || new URL("../stable", import.meta.url).pathname);
const port = +(process.env.PORT || 5198);
const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".jpg": "image/jpeg", ".png": "image/png", ".woff2": "font/woff2", ".svg": "image/svg+xml" };
http.createServer((req, res) => {
  let p = decodeURIComponent(req.url.split("?")[0]); if (p.endsWith("/")) p += "index.html";
  const f = path.join(root, path.normalize(p));
  if (!f.startsWith(root)) { res.writeHead(403); return res.end(); }
  fs.stat(f, (e, st) => {
    if (e || !st.isFile()) { res.writeHead(404); return res.end("not found"); }
    res.writeHead(200, { "content-type": types[path.extname(f)] || "application/octet-stream", "content-length": st.size, "cache-control": p.includes("/assets/") || p.includes("/data/") ? "public, max-age=3600" : "no-cache" });
    fs.createReadStream(f).pipe(res);
  });
}).listen(port, "127.0.0.1", () => console.log(`${root} on ${port}`));
