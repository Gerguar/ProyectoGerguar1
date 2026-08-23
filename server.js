const http = require("http");
const fs = require("fs");
const path = require("path");

const root = __dirname;
const port = Number(process.env.PORT || 3000);
const blockedFiles = new Set([
  ".htaccess",
  ".ftp-deploy-sync-state-v2.json",
  "claude_debug.json",
  "package.json",
  "server.js"
]);

const mimeTypes = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".ico": "image/x-icon"
};

function sendFile(res, filePath) {
  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("Not found");
      return;
    }

    const ext = path.extname(filePath).toLowerCase();
    const headers = {
      "Content-Type": mimeTypes[ext] || "application/octet-stream",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "strict-origin-when-cross-origin",
      "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; img-src 'self' https: data:; connect-src 'self' https://dyeouwqtebrvioesrbcf.supabase.co; font-src 'self' data: https://fonts.gstatic.com; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'; upgrade-insecure-requests"
    };

    if (path.basename(filePath) === "predictions.json") {
      headers["Cache-Control"] = "public, max-age=300";
    } else {
      headers["Cache-Control"] = "public, max-age=3600";
    }

    res.writeHead(200, headers);
    res.end(data);
  });
}

const server = http.createServer((req, res) => {
  if (req.method !== "GET" && req.method !== "HEAD") {
    res.writeHead(405, {
      "Content-Type": "text/plain; charset=utf-8",
      "Allow": "GET, HEAD"
    });
    res.end("Method not allowed");
    return;
  }

  let pathname;
  try {
    const url = new URL(req.url, "http://localhost");
    pathname = decodeURIComponent(url.pathname);
  } catch {
    res.writeHead(400, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("Bad request");
    return;
  }

  if (pathname.includes("\0") || blockedFiles.has(path.basename(pathname))) {
    res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("Not found");
    return;
  }

  if (pathname === "/") {
    pathname = "/index.html";
  }

  const requested = path.resolve(root, `.${pathname}`);
  if (requested !== root && !requested.startsWith(root + path.sep)) {
    res.writeHead(403, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("Forbidden");
    return;
  }

  fs.stat(requested, (err, stat) => {
    if (!err && stat.isFile()) {
      sendFile(res, requested);
      return;
    }

    sendFile(res, path.join(root, "index.html"));
  });
});

server.listen(port, "0.0.0.0", () => {
  console.log(`FutVersus listening on port ${port}`);
});

module.exports = server;
