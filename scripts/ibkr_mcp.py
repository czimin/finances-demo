"""
ibkr_mcp.py — read your IBKR account through Interactive Brokers' MCP server.

    python scripts/ibkr_mcp.py login              # once: sign in to IBKR in your browser
    python scripts/ibkr_mcp.py status             # who is signed in, until when
    python scripts/ibkr_mcp.py tools              # the tools the server offers
    python scripts/ibkr_mcp.py call <tool> ['{"arg": "value"}']
    python scripts/ibkr_mcp.py positions [--tool NAME] [--args JSON]
    python scripts/ibkr_mcp.py logout             # forget the stored token

IBKR runs a hosted Model Context Protocol server (MCP_URLS below). Any MCP
client can use it, and this is one, written with the standard library only, so
nothing here needs an AI assistant. It speaks MCP's Streamable HTTP transport
(JSON-RPC over HTTPS, JSON or server-sent-event replies) and signs in with
OAuth 2.1, the way IBKR's server advertises:

  1. the server's 401 names its protected-resource metadata, which names the
     authorization server (https://api.ibkr.com);
  2. this script registers itself there as a public client (dynamic client
     registration, no secret), with a loopback redirect on 127.0.0.1;
  3. your browser opens IBKR's own sign-in page — your password goes to IBKR,
     never through this script — and IBKR redirects back with a code;
  4. the code is exchanged for a token using PKCE (S256).

Read-only by construction: it asks only for the `mcp.read` scope, so IBKR
won't let the token stage or submit orders, and `call` also refuses any tool
whose name looks like order entry. Revoke access any time in Client Portal >
Settings > Manage Third-Party Consents.

The token is kept in data/ibkr_mcp.json (data/ is never committed). `snapshot.py
--ibkr-mcp` uses `positions` to take the foreign-stock rows from IBKR live
instead of from an Activity Statement CSV.
"""
import argparse
import base64
import hashlib
import json
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# IBKR's MCP server answers at two addresses: .../mcp, the one behind IBKR's
# connector in Claude's marketplace, and .../mcp-public, the one IBKR publishes
# for MCP clients of your own. `login` tries them in this order and keeps the
# first that actually serves this client.
MCP_URLS = ("https://api.ibkr.com/v1/api/mcp", "https://api.ibkr.com/v1/api/mcp-public")
MCP_URL = MCP_URLS[-1]                  # used only if no address has been stored yet
SCOPE = "mcp.read"                      # never mcp.write / mcp.orders.*
REDIRECT_PORT = 33418                   # loopback port registered as the redirect
PROTOCOL = "2025-06-18"                 # MCP protocol revision this client speaks
STORE = ROOT / "data" / "ibkr_mcp.json"
TIMEOUT = 30
LOGIN_WAIT = 300                        # seconds to finish signing in
ORDER_LIKE = re.compile(r"order|trade|submit|stage|cancel|modify|place", re.I)
POSITIONS_TOOL = None                   # set to a tool name if `positions` guesses wrong
USER_AGENT = "finances-ibkr-mcp/1.0"


class McpError(Exception):
    pass


class ParseError(Exception):
    """The server answered, but not with holdings this script can read safely."""


# ---------- HTTP ----------

def _check_url(url):
    u = urllib.parse.urlparse(url)
    local = u.hostname in ("127.0.0.1", "localhost", "::1")
    if u.scheme != "https" and not (u.scheme == "http" and local):
        raise McpError(f"refusing a non-HTTPS URL: {url}")


def http(method, url, body=None, headers=None, form=False):
    """(status, headers, bytes). Never raises on an HTTP error status."""
    _check_url(url)
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    data = None
    if body is not None:
        if form:
            data = urllib.parse.urlencode(body).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()
    except urllib.error.URLError as e:
        raise McpError(f"couldn't reach {urllib.parse.urlparse(url).netloc}: {e.reason}")


def get_json(url):
    status, _, raw = http("GET", url)
    if status != 200:
        raise McpError(f"{url} answered HTTP {status}")
    return json.loads(raw)


# ---------- token store ----------

def load():
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(state):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    tmp.replace(STORE)


# ---------- OAuth ----------

def discover(mcp_url):
    """Protected-resource metadata -> authorization-server metadata."""
    status, headers, _ = http("POST", mcp_url, {"jsonrpc": "2.0", "id": 0, "method": "ping"},
                              {"Accept": "application/json, text/event-stream"})
    meta_url = None
    m = re.search(r'resource_metadata="([^"]+)"', headers.get("WWW-Authenticate", "") or "")
    if status == 401 and m:
        meta_url = m.group(1)
    resource = get_json(meta_url or mcp_url.rstrip("/") + "/.well-known/oauth-protected-resource")
    if SCOPE not in resource.get("scopes_supported", [SCOPE]):
        raise McpError(f"the server doesn't offer the {SCOPE} scope")
    issuer = resource["authorization_servers"][0].rstrip("/")
    auth = get_json(issuer + "/.well-known/oauth-authorization-server")
    if auth.get("issuer", "").rstrip("/") != issuer:
        raise McpError("authorization server metadata names a different issuer")
    if "S256" not in auth.get("code_challenge_methods_supported", []):
        raise McpError("authorization server doesn't support PKCE S256")
    for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        if key not in auth:
            raise McpError(f"authorization server metadata has no {key}")
        _check_url(auth[key])
    return resource.get("resource", mcp_url), auth


def register(auth, redirect_uri):
    status, _, raw = http("POST", auth["registration_endpoint"], {
        "client_name": "finances (personal ledger, read-only)",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": SCOPE,
    })
    if status not in (200, 201):
        raise McpError(f"client registration failed (HTTP {status}): {raw[:300].decode(errors='replace')}")
    return json.loads(raw)["client_id"]


def _callback_server(port, expected_state, got):
    """Bind the loopback redirect listener; the matching redirect fills `got`."""

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if urllib.parse.urlparse(self.path).path != "/callback" or q.get("state") != expected_state:
                self.send_error(400, "unexpected request")
                return
            got.update(q)
            ok = "code" in q
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(("<p>Signed in — you can close this tab.</p>" if ok else
                              "<p>Sign-in was not completed. Check the terminal.</p>").encode())

        def log_message(self, *a):
            pass

    try:
        server = HTTPServer(("127.0.0.1", port), Callback)
    except OSError:
        raise McpError(f"port {port} is busy — close whatever uses it and run login again")
    server.timeout = 1
    return server


def _wait_for_code(server, got, cancel):
    """Serve redirects until the expected one arrives, LOGIN_WAIT passes or
    `cancel` is set. Always releases the port."""
    deadline = time.time() + LOGIN_WAIT
    try:
        while not got and not cancel.is_set() and time.time() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    return got


def _token_request(auth, form):
    status, _, raw = http("POST", auth["token_endpoint"], form, form=True)
    if status != 200:
        raise McpError(f"token request failed (HTTP {status}): {raw[:300].decode(errors='replace')}")
    tok = json.loads(raw)
    if "access_token" not in tok:
        raise McpError("token response carried no access_token")
    return tok


def _store_token(state, tok):
    state["access_token"] = tok["access_token"]
    state["refresh_token"] = tok.get("refresh_token", state.get("refresh_token"))
    state["expires_at"] = time.time() + int(tok.get("expires_in", 3600))
    state["scope"] = tok.get("scope", SCOPE)
    save(state)


def _login_one(mcp_url, open_browser):
    """The OAuth sign-in against one address; stores the token."""
    resource, auth = discover(mcp_url)
    redirect_uri = f"http://127.0.0.1:{REDIRECT_PORT}/callback"
    state = load()
    if state.get("issuer") != auth["issuer"] or state.get("redirect_uri") != redirect_uri \
            or state.get("resource") != resource:
        state = {"issuer": auth["issuer"], "redirect_uri": redirect_uri, "resource": resource,
                 "client_id": register(auth, redirect_uri)}
    state.update(token_endpoint=auth["token_endpoint"], mcp_url=mcp_url)

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    nonce = secrets.token_urlsafe(24)
    url = auth["authorization_endpoint"] + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": state["client_id"], "redirect_uri": redirect_uri,
        "scope": SCOPE, "state": nonce, "code_challenge": challenge,
        "code_challenge_method": "S256", "resource": resource})

    result, cancel = {}, threading.Event()
    server = _callback_server(REDIRECT_PORT, nonce, result)    # listening before the browser opens
    waiter = threading.Thread(target=_wait_for_code, args=(server, result, cancel), daemon=True)
    waiter.start()
    print(f"Sign in to IBKR in the browser window that opens (read-only access) — {mcp_url}")
    print(f"If it doesn't open, visit:\n  {url}")
    print("If IBKR shows an error page instead of sending you back, press Ctrl-C here.\n")
    if open_browser:
        webbrowser.open(url)
    try:
        while waiter.is_alive():
            waiter.join(0.5)
    except KeyboardInterrupt:
        cancel.set()
        waiter.join()                   # frees the port for the next attempt
        raise McpError("sign-in abandoned")
    if not result:
        raise McpError("timed out waiting for the IBKR sign-in to finish")
    if "code" not in result:
        raise McpError(f"IBKR refused: {result.get('error')} {result.get('error_description', '')}".strip())
    if "iss" in result and result["iss"].rstrip("/") != auth["issuer"].rstrip("/"):
        raise McpError("the sign-in came back from an unexpected issuer — not trusting it")
    tok = _token_request(auth, {
        "grant_type": "authorization_code", "code": result["code"], "redirect_uri": redirect_uri,
        "client_id": state["client_id"], "code_verifier": verifier, "resource": resource})
    _store_token(state, tok)


def login(urls=None, open_browser=True):
    """Sign in at the first address that works, proved by listing its tools."""
    urls = (urls,) if isinstance(urls, str) else tuple(urls or MCP_URLS)
    for i, url in enumerate(urls):
        try:
            _login_one(url, open_browser)
            with Session(url) as s:
                count = len(s.tools())
        except McpError as e:
            state = load()
            for key in ("access_token", "refresh_token", "expires_at"):
                state.pop(key, None)     # never leave a token for an address that failed
            if state:
                save(state)
            if i == len(urls) - 1:
                raise
            print(f"  {url} didn't work for this client ({e}) — trying {urls[i + 1]}\n")
            continue
        where = STORE.relative_to(ROOT) if STORE.is_relative_to(ROOT) else STORE
        print(f"Signed in through {url}: {count} tools available. Token saved to {where} "
              f"(scope: {load().get('scope')}).")
        return url


def access_token():
    """A valid access token, refreshed if needed."""
    state = load()
    if not state.get("access_token"):
        raise McpError("not signed in — run: python scripts/ibkr_mcp.py login")
    if time.time() > state.get("expires_at", 0) - 60:
        if not state.get("refresh_token"):
            raise McpError("the IBKR sign-in has expired — run: python scripts/ibkr_mcp.py login")
        tok = _token_request({"token_endpoint": state["token_endpoint"]}, {
            "grant_type": "refresh_token", "refresh_token": state["refresh_token"],
            "client_id": state["client_id"], "resource": state["resource"]})
        _store_token(state, tok)
    return state["access_token"], state


# ---------- MCP (Streamable HTTP) ----------

class Session:
    def __init__(self, url=None):
        token, state = access_token()
        self.url = url or state.get("mcp_url", MCP_URL)
        self.token, self.sid, self.version, self.next_id = token, None, PROTOCOL, 1

    def _post(self, message):
        headers = {"Accept": "application/json, text/event-stream",
                   "Authorization": f"Bearer {self.token}", "MCP-Protocol-Version": self.version}
        if self.sid:
            headers["Mcp-Session-Id"] = self.sid
        status, h, raw = http("POST", self.url, message, headers)
        if status == 401:
            raise McpError("IBKR rejected the token — run: python scripts/ibkr_mcp.py login")
        if status >= 400:
            raise McpError(f"MCP server answered HTTP {status}: {raw[:300].decode(errors='replace')}")
        return h, raw

    def request(self, method, params=None):
        rid = self.next_id
        self.next_id += 1
        h, raw = self._post({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        if h.get("Mcp-Session-Id"):
            self.sid = h["Mcp-Session-Id"]
        msg = self._find(raw.decode("utf-8", errors="replace"), h.get("Content-Type", ""), rid)
        if "error" in msg:
            raise McpError(f"{method}: {msg['error'].get('message', msg['error'])}")
        return msg.get("result", {})

    @staticmethod
    def _find(text, ctype, rid):
        """The JSON-RPC reply with id `rid`, from a JSON body or an SSE stream."""
        if "text/event-stream" in ctype:
            for event in re.split(r"\r?\n\r?\n", text):
                data = "\n".join(l[5:].lstrip() for l in event.splitlines() if l.startswith("data:"))
                if data:
                    msg = json.loads(data)
                    if isinstance(msg, dict) and msg.get("id") == rid:
                        return msg
            raise McpError("the server's event stream ended without a reply")
        msg = json.loads(text)
        if isinstance(msg, list):
            msg = next((m for m in msg if m.get("id") == rid), {})
        return msg

    def __enter__(self):
        res = self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                          "clientInfo": {"name": "finances", "version": "1.0"}})
        self.version = res.get("protocolVersion", PROTOCOL)
        self.server = res.get("serverInfo", {})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self

    def __exit__(self, *exc):
        if self.sid:      # end the session politely; failure here doesn't matter
            try:
                http("DELETE", self.url, headers={"Authorization": f"Bearer {self.token}",
                                                  "Mcp-Session-Id": self.sid})
            except McpError:
                pass

    def tools(self):
        out, cursor = [], None
        while True:
            res = self.request("tools/list", {"cursor": cursor} if cursor else {})
            out += res.get("tools", [])
            cursor = res.get("nextCursor")
            if not cursor:
                return out

    def call(self, name, arguments=None):
        if ORDER_LIKE.search(name):
            raise McpError(f"refusing to call {name!r}: this client is read-only")
        res = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        if res.get("isError"):
            raise McpError(f"{name} failed: {text_of(res)[:500]}")
        return res


def text_of(result):
    return "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")


def payload(result):
    """A tool result as data: structuredContent, else its text parsed as JSON."""
    if "structuredContent" in result:
        return result["structuredContent"]
    text = text_of(result)
    try:
        return json.loads(text)
    except ValueError:
        return text


# ---------- positions -> snapshot rows ----------

FIELDS = {     # names IBKR's Web API uses for a position, plus common spellings
    "qty": ("position", "quantity", "qty", "pos"),
    "price": ("mktPrice", "marketPrice", "lastPrice", "price", "close"),
    "value": ("mktValue", "marketValue", "value", "positionValue"),
    "avg": ("avgPrice", "averageCost", "avgCost", "costBasisPrice", "averagePrice"),
    "ccy": ("currency", "ccy"),
    "pnl": ("unrealizedPnl", "unrealizedPnL", "unrealized_pnl", "unrealizedPNL"),
    "symbol": ("ticker", "symbol", "localSymbol"),
    "desc": ("contractDesc", "description", "name", "fullName"),
}


def _pick(row, key):
    return next((row[k] for k in FIELDS[key] if row.get(k) not in (None, "")), None)


def _position_lists(data):
    """Every list of dicts in `data` that looks like positions."""
    if isinstance(data, list):
        if data and all(isinstance(r, dict) for r in data) and \
                all(_pick(r, "qty") is not None and _pick(r, "symbol") for r in data):
            yield data
        for item in data:
            yield from _position_lists(item)
    elif isinstance(data, dict):
        for v in data.values():
            yield from _position_lists(v)


def positions(tool=None, arguments=None, url=None):
    """[(ticker, qty, ccy, price, avg, pnl, value_in_ccy, desc)] read live from IBKR."""
    with Session(url) as s:
        names = [t["name"] for t in s.tools()]
        name = tool or POSITIONS_TOOL or next(
            (n for n in names if "position" in n.lower() and not ORDER_LIKE.search(n)), None)
        if name not in names:
            raise ParseError(f"no positions tool found among: {', '.join(names) or 'none'} — "
                             "pick one with --tool (see: python scripts/ibkr_mcp.py tools)")
        data = payload(s.call(name, arguments))
    found = next(_position_lists(data), None)
    if found is None:
        raise ParseError(f"{name} didn't return a list of positions this script recognises "
                         f"(try: python scripts/ibkr_mcp.py call {name})")
    out = []
    for r in found:
        qty, price, ccy = _pick(r, "qty"), _pick(r, "price"), _pick(r, "ccy")
        if qty in (None, 0) or price is None or not ccy:
            if qty in (None, 0):
                continue                       # a closed position
            raise ParseError(f"position {_pick(r, 'symbol')} lacks a price or currency")
        qty, price = float(qty), float(price)
        avg = _pick(r, "avg")
        value = _pick(r, "value")
        value = float(value) if value is not None else qty * price
        pnl = _pick(r, "pnl")
        pnl = float(pnl) if pnl is not None else (qty * (price - float(avg)) if avg is not None else None)
        sym = str(_pick(r, "symbol")).split()[0]
        out.append((sym, qty, str(ccy).upper(), price, float(avg) if avg is not None else None,
                    pnl, value, str(_pick(r, "desc") or sym)))
    return out


def snapshot_rows(snap_date, fx, base="SGD", tool=None, arguments=None):
    """stocks_foreign rows in exactly the format read_ibkr writes (see "Note formats are a contract"
    in README.md).

    `fx(ccy, date)` gives `base` per 1 `ccy` (snapshot.py passes its fx_rate).
    """
    rows = []
    for sym, qty, ccy, price, avg, pnl, value, desc in positions(tool, arguments):
        if avg is None:
            raise ParseError(f"{sym}: no average cost — returns.py and the sparklines need it")
        note = f"{desc}: {qty:g} @ {ccy} {price:.2f}; avg {avg:.2f}; P&L {pnl:+.2f}"
        rate = 1.0 if ccy == base else fx(ccy, snap_date)
        rows.append(("stocks_foreign", sym.lower(), round(value * rate, 2), note))
    if not rows:
        raise ParseError("IBKR reported no open positions")
    return rows


# ---------- command line ----------

def main():
    ap = argparse.ArgumentParser(description="Read your IBKR account through IBKR's MCP server (read-only).")
    ap.add_argument("--url", default=None,
                    help="MCP server address (default: try " + ", then ".join(MCP_URLS) + ")")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lg = sub.add_parser("login", help="sign in to IBKR in the browser")
    lg.add_argument("--no-browser", action="store_true", help="print the sign-in URL only")
    sub.add_parser("status", help="show the stored sign-in")
    sub.add_parser("logout", help="forget the stored token")
    sub.add_parser("tools", help="list the server's tools")
    c = sub.add_parser("call", help="call one tool and print what it returns")
    c.add_argument("tool")
    c.add_argument("arguments", nargs="?", default="{}", help="JSON object of arguments")
    p = sub.add_parser("positions", help="print holdings as snapshot.py would record them")
    p.add_argument("--tool", help="the positions tool, if the guess is wrong")
    p.add_argument("--args", default="{}", help="JSON object of arguments for it")
    a = ap.parse_args()

    try:
        if a.cmd == "login":
            login((a.url,) if a.url else MCP_URLS, open_browser=not a.no_browser)
        elif a.cmd == "status":
            st = load()
            if not st.get("access_token"):
                print("Not signed in.")
            else:
                left = st.get("expires_at", 0) - time.time()
                print(f"Signed in to {st.get('mcp_url')} (scope {st.get('scope')}); access token "
                      + (f"valid {left / 60:.0f} more min" if left > 0 else "expired")
                      + (", refreshable." if st.get("refresh_token") else "."))
        elif a.cmd == "logout":
            STORE.unlink(missing_ok=True)
            print("Token forgotten. To revoke it at IBKR too: Client Portal > Settings > "
                  "Manage Third-Party Consents.")
        elif a.cmd == "tools":
            with Session(a.url) as s:
                print(f"{s.server.get('name', 'server')} {s.server.get('version', '')} — MCP {s.version}\n")
                for t in s.tools():
                    flag = "  (order-like: blocked here)" if ORDER_LIKE.search(t["name"]) else ""
                    props = (t.get("inputSchema") or {}).get("properties", {})
                    print(f"{t['name']}({', '.join(props)}){flag}")
                    if t.get("description"):
                        print("    " + t["description"].strip().splitlines()[0][:110])
        elif a.cmd == "call":
            with Session(a.url) as s:
                data = payload(s.call(a.tool, json.loads(a.arguments)))
            print(data if isinstance(data, str) else json.dumps(data, indent=2))
        elif a.cmd == "positions":
            rows = positions(a.tool, json.loads(a.args), a.url)
            for sym, qty, ccy, price, avg, pnl, value, desc in rows:
                cost = f"avg {avg:,.2f}" if avg is not None else "avg ?"
                print(f"  {sym:8} {qty:>12g} @ {ccy} {price:>10,.2f}  {cost:>14}  "
                      f"value {ccy} {value:>12,.2f}  P&L {pnl if pnl is not None else 0:+,.2f}  {desc}")
    except (McpError, ParseError) as e:
        sys.exit(f"!! {e}")


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    main()
