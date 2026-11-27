"""serve.py — one command to view the dashboard.

Refreshes the data export, starts a small HTTP server on localhost and opens
dashboard.html in your browser.

    python serve.py                # export, serve on :8000, open the dashboard
    python serve.py --port 9000    # pick a different port
    python serve.py --no-export    # skip the re-export
    python serve.py --no-open      # don't launch a browser
    python serve.py --keep         # keep serving (reloads work) until Ctrl-C

By default it stops by itself once the page has loaded: the data file is the
last thing the page asks this server for (fonts come from Google), so after
handing it out there is nothing left to serve. The open tab keeps working;
reloading it needs another run (or --keep). Pure standard library.

The server hands out exactly two files — the dashboard and its data — and
nothing else: not the database, not the statements in data/, not the source.
It also refuses requests whose Host header isn't this machine, which is what
stops a malicious web page from reading the data through DNS rebinding.
"""
import argparse
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import export
from db import ROOT

FILES = {
    "/": ("dashboard.html", "text/html; charset=utf-8"),
    "/dashboard.html": ("dashboard.html", "text/html; charset=utf-8"),
    "/dashboard_data.js": ("dashboard_data.js", "text/javascript; charset=utf-8"),
}
# The page may run its own inline script/styles, load its data file, and fetch
# the two Google fonts. Nothing else — no other network access at all.
CSP = ("default-src 'none'; script-src 'self' 'unsafe-inline'; "
       "style-src 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src data:; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class Handler(BaseHTTPRequestHandler):
    server_version = "finances"
    sys_version = ""

    def do_GET(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host not in ("127.0.0.1", "localhost"):
            return self.send_error(403, "Forbidden host")
        entry = FILES.get(self.path.split("?", 1)[0])
        if entry is None:
            return self.send_error(404)
        name, ctype = entry
        try:
            body = (ROOT / name).read_bytes()
        except OSError:
            return self.send_error(404, f"{name} is missing — run python export.py")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")      # always the fresh export
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        if self.command == "GET":
            self.wfile.write(body)
            if name == "dashboard_data.js" and not self.server.keep:
                self.wfile.flush()
                # shutdown() waits for serve_forever() to return, so it must run
                # off this request's thread.
                threading.Thread(target=self.server.shutdown, daemon=True).start()

    do_HEAD = do_GET

    def log_message(self, *args):       # keep the terminal quiet
        pass


def main():
    ap = argparse.ArgumentParser(description="Serve the finances dashboard.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-export", action="store_true", help="skip the data re-export")
    ap.add_argument("--no-open", action="store_true", help="don't open a browser")
    ap.add_argument("--keep", action="store_true",
                    help="keep serving after the page loads (until Ctrl-C)")
    args = ap.parse_args()

    if not args.no_export:
        export.main()

    # 127.0.0.1 (not 0.0.0.0): this is personal data — keep it off the network.
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    httpd.keep = args.keep
    url = f"http://127.0.0.1:{args.port}/dashboard.html"
    print(f"Serving the dashboard at {url}\n"
          + ("Press Ctrl-C to stop." if args.keep else "Stops once the page has loaded (Ctrl-C to stop sooner)."))

    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
        if not args.keep:
            print("Page loaded; server stopped.")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
