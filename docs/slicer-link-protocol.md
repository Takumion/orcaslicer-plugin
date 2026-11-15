# Slicer link protocol — v1

How Takumion (web or desktop) sends bodies to OrcaSlicer through the **Takumion
Link** Orca plugin. This page is the whole contract between the two: the plugin
repository carries a verbatim copy, and neither side relies on anything not
written here.

- **Plugin:** listens on loopback, writes the files, puts them on Orca's plate,
  tracks which plate objects are linked to which bodies.
- **Takumion:** exports meshes, pairs once, sends.

Two transports carry the same messages:

| Transport | Used by | Pairing |
|---|---|---|
| HTTP/JSON on `127.0.0.1` | Takumion in a browser or the desktop app | Required |
| `window.orca` bridge | Takumion running inside Orca's plugin tab | Not needed (a bridge key instead) |

## Discovery

The plugin binds **`127.0.0.1` only**, on the first free port of
**47390, 47391, 47392, 47393, 47394**. A client probes them in that order with
`GET /v1/hello` and uses the first answer whose `app` is `"takumion-orca-link"`.
A client remembers the last port that answered and tries it first.

No answer on any port means Orca is not running, or runs without the plugin.
The desktop app may then start Orca (no file argument) and probe again for up to
30 s; the web app shows how to start it. What a client does when no plugin is
installed at all (hand the files to Orca itself, offer a download) is outside
this protocol.

## Security

- **Loopback only.** Requests whose `Host` is not `127.0.0.1:<port>` or
  `localhost:<port>` are refused with `421` — this stops DNS-rebinding pages.
- **Pairing.** Every endpoint except `hello` and `pair` needs
  `Authorization: Bearer <token>`. A token is issued by `pair` and is bound to the
  `Origin` it was issued to; the same token from another origin is refused.
  A request without an `Origin` header (a native client) is bound to the
  pseudo-origin `native:<clientName>`.
- **Pairing codes** are shown in the plugin's Orca tab only after the user clicks
  *Pair an app*. A code has 6 digits, lives 120 s, and dies after 5 wrong tries.
  A wrong code and "no code open" get the same refusal, so a page cannot tell when
  a code goes live. A request whose `Origin` is `null` (sandboxed frames, `data:`
  pages — all sharing that one value) cannot pair.
- **Tokens** are 32 random bytes, base64url. The plugin stores only their SHA-256,
  with origin, client name, created and last-used times. The user can revoke any
  pairing from the Orca tab.
- **CORS.** `hello` and `pair` answer any origin (they reveal nothing and need a
  code). `GET /tab` never sends `Access-Control-Allow-Origin`: its page carries the
  tab nonce (see *Bridge transport*). Every other endpoint echoes `Access-Control-Allow-Origin` only for a
  paired origin — except an **error reply**, which always echoes the request's
  origin: a refusal (`401` after the user revoked the pairing in Orca, `400`,
  `413`, `421`) reveals nothing, and without the header the browser hides it
  behind an opaque network error, so the client could not tell "pair again" from
  "connection lost". **Preflights** (`OPTIONS`) are answered for any origin, with
  that origin, `Access-Control-Allow-Methods: GET, POST, DELETE` and
  `Access-Control-Allow-Headers: Authorization, Content-Type`: they carry no data,
  and the actual reply decides. Without that, a revoked origin's `send` (JSON with
  `Authorization`, so always preflighted) never goes out and its `401` is never
  seen. Preflights carrying `Access-Control-Request-Private-Network`
  are answered with `Access-Control-Allow-Private-Network: true` (Chrome's
  private/local network access).
- **Limits.** Request body ≤ 384 MB (`413` above); a `pair` body ≤ 4 KB, refused
  unread above that. Keys are
  `[A-Za-z0-9_-]{1,128}`; anything else is `400`.

## Messages

All bodies are JSON, UTF-8. Mesh bytes travel as standard base64.

### `GET /v1/hello`

```json
{
  "app": "takumion-orca-link",
  "protocol": 1,
  "plugin": {"version": "0.1.0"},
  "orca": {"version": "2.5.1"},
  "paired": true,
  "setup": {"loadGeometryOnly": "unknown"},
  "files": {"folder": "/home/me/Documents/Takumion", "ready": true, "needsRestart": false}
}
```

- `paired` is `true` when the request carries a valid token for its origin.
- `files` — where the plugin writes Takumion's files — is present **only when
  `paired` is `true`** (and always over the bridge): the folder path names the
  computer's user. `ready` is `false` until a folder is in use; `needsRestart`
  is `true` when a newly chosen folder waits for Orca's next start. A client
  warns before sending while `ready` is `false` (see *Files on disk*).
- `setup.loadGeometryOnly` is `"unknown"` until the plugin has seen a 3MF land
  as plain geometry (then `"confirmed"`) or open the dialog / the project
  (then `"missing"`). See [3MF](#3mf-and-orcas-load-behavior).

### `POST /v1/pair`

```json
{"code": "418207", "clientName": "Takumion (Chrome)"}
```
→ `200 {"token": "…"}`. Wrong or expired code → `403 forbidden`.

### `DELETE /v1/pair`

Revokes the caller's own token. → `204`.

### `POST /v1/send`

```json
{
  "doc": {"key": "prj_8f2c", "name": "Bracket"},
  "mode": "update",
  "format": "3mf",
  "parts": [
    {"bodyKey": "body_1", "name": "Base", "data": "UEsDBBQ…"},
    {"bodyKey": "body_2", "name": "Lid",  "data": "UEsDBBQ…"}
  ]
}
```

- One request per send, all parts in it. Each part is one body, one file, in
  millimetres, Z up.
- `mode`:
  - `"update"` — keep one file per body. The first send of a body writes it and
    puts it on the plate. Later sends overwrite that same file; if the linked
    object is still on the plate it is **not** added again, and the user is told
    to *Reload from disk*. If it is gone from the plate, the file is put on the
    plate again.
  - `"add"` — write a new, time-stamped file and put it on the plate. Never linked.
- `format`: `"3mf"` or `"stl"`.

→ `200`:

```json
{
  "results": [
    {"bodyKey": "body_1", "action": "updated", "onPlate": true,  "reloadNeeded": true},
    {"bodyKey": "body_2", "action": "added",   "onPlate": true,  "reloadNeeded": false}
  ],
  "needsOrcaSetup": false
}
```

- `action`: `"added"` (put on the plate), `"updated"` (file overwritten, object
  already on the plate) or `"failed"` (with `message`).
- `onPlate`: `true` when the plugin saw the object arrive (lifecycle event with
  the file as its source) within 5 s, `false` when it did not, `null` when it was
  not waited for (`updated`).
- `needsOrcaSetup`: a 3MF did not land as plain geometry — show the setup hint.
- The plugin answers after the hand-off, so a send takes up to ~5 s.

### `GET /v1/links?doc=<key>`

```json
{
  "doc": {"key": "prj_8f2c", "name": "Bracket"},
  "bodies": [
    {"bodyKey": "body_1", "name": "Base", "format": "3mf",
     "lastSent": "2026-11-15T10:50:30Z", "onPlate": true, "reloadNeeded": true}
  ]
}
```

`reloadNeeded` clears when Orca reports the object changed after the last send.

### Errors

```json
{"error": {"code": "unauthorized", "message": "Pair this app in Orca's Takumion tab first."}}
```

| HTTP | `code` | Meaning |
|---|---|---|
| 400 | `bad-request` | Malformed JSON, bad key, unknown `mode`/`format`, empty `parts` |
| 401 | `unauthorized` | No token, or not valid for this origin |
| 403 | `forbidden` | Wrong or expired pairing code |
| 409 | `setup-needed` | No folder for Takumion's files is in use yet (or one waits for Orca's restart); `message` says what to do |
| 413 | `too-large` | Body over the limit |
| 421 | `bad-host` | `Host` is not loopback |
| 503 | `orca-busy` | Orca is starting or the hand-off target is not ready; retry |
| 500 | `internal` | Anything else; `message` says what |

A client never parses `message`; it shows it.

## Bridge transport (Takumion inside Orca)

Inside the plugin's Orca tab the editor talks over `window.orca` instead of HTTP.
Same method names and payloads, wrapped:

```json
{"type": "takumion-link", "id": 7, "method": "send", "params": {"doc": {…}, "mode": "add", "format": "3mf", "parts": […]}}
```
reply, delivered to `window.orca.onMessage`:
```json
{"type": "takumion-link", "id": 7, "result": {"results": […], "needsOrcaSetup": false}}
```
or `{"type": "takumion-link", "id": 7, "error": {"code": "…", "message": "…"}}`.

Methods: `hello`, `send`, `links`, and one that exists only here: **`show-panel`**
opens the plugin's own page (status, pairing, linked files) in its own Orca
window, so Takumion stays loaded in its tab; its result is `{"shown": true}`, or
`false` on an Orca without plugin windows (the client then navigates the tab to
`tab.url`, below). No pairing, but every call carries a
**bridge `key`**:

```json
{"type": "takumion-link", "id": 7, "key": "…", "method": "hello", "params": {}}
```

Orca injects `window.orca` into **any** page its plugin tab shows — Takumion, and
whatever a link from Takumion opens there (Orca loads new-window links in the same
tab), and it does not tell the plugin which page sent a message. So:

- The plugin makes a random key per load and puts it only in the **URL fragment**
  of the *In this tab* link: `<takumionUrl>?host=orca#orcaLinkKey=<key>`. A
  fragment never reaches a server or a `Referer`.
- The editor moves the key into its tab's `sessionStorage` at boot (same origin
  only; it survives a reload) and removes it from the URL.
- A call without the right key is answered `forbidden` ("This page was not opened
  by the Takumion Link plugin."). One `send` runs at a time; a second one while it
  runs is `orca-busy`.

The plugin's own tab page is driven the same way: it is rendered with a random
**tab nonce** (`<meta name="takumion-tab">`), sends it with every
`{"type": "tab", …}` message, and the plugin ignores tab messages without it. The
plugin pushes its tab state — which holds a live pairing code — only while its own
page is showing: from that page's first message until it says `leave`, which it
does before navigating the tab to Takumion.

**Getting there and back.** The tab opens Takumion by itself: the first page Orca
shows in it goes on to Takumion as soon as Takumion answers (plugin setting
`openTakumionInTab`, `true` by default; offline, it stays on the plugin's page and
says so). The plugin's page also has an *Open Takumion › In this tab*
button. Both navigate the tab to `<takumionUrl>?host=orca#orcaLinkKey=<key>`
(`takumionUrl` is the plugin's setting: `https://app.takumion.com/` by default; a
plain-`http` URL is accepted only for `localhost` / `127.0.0.1`, for a local dev
server). Orca injects `window.orca` into the page after the navigation.
Over the bridge, `hello`'s result carries one more, optional field:

```json
{"app": "takumion-orca-link", "protocol": 1, "…": "…", "tab": {"url": "http://127.0.0.1:47390/tab"}}
```

`GET /tab` on the loopback server serves the plugin's own tab page (HTML with the
tab nonce, `Host`-checked like every endpoint, never readable cross-origin; its
state comes over `window.orca`). The editor's "back to Takumion Link" navigates there. `tab` is
optional and absent over HTTP, so it needs no protocol bump.

## 3MF and Orca's load behavior

With Orca's default *Load behavior* (`ask_when_relevant`), a 3MF dropped on a
plate that has objects opens a "project or geometry" dialog, and a 3MF on an
**empty** plate is opened **as the project** — Orca's next *Save* would then
write the whole project into the link file. Setting **Preferences › Load
behavior → Load geometry only** turns every 3MF into a plain import.

A plugin cannot read that preference, so it infers it (`setup.loadGeometryOnly`)
and guards:

- no `ObjectAdded` for a handed-off 3MF within 5 s → `needsOrcaSetup: true`, and
  the Orca tab shows the setup card again;
- Orca's project path equals a link file → the plugin warns before the user saves,
  and drops that file from the links (the next send writes a fresh one).

STL never meets the dialog.

## Files on disk

The meshes go into **one folder the user chooses** in the plugin's page (*Where
Takumion keeps its files*; suggested `~/Documents/Takumion`). Until one is in
use, `send` is refused with `setup-needed`. Orca's sandbox shapes the rest:

- A plugin may ask for a whole folder only while Orca loads it, so a newly
  chosen folder is used from Orca's **next start** (Orca asks once to allow it).
- Orca asks about every folder a plugin creates outside its data folder and never
  remembers the answer, so the plugin creates the chosen folder once, when it is
  saved, and keeps the files in it **flat**:

```
<folder>/
  <Project> - <Body>.<fmt>                     update mode: path fixed at first send
  <Project> - <Body> <YYYY-MM-DD HHMMSS>.<fmt>  add mode
```

A body's link path is decided at its first send and never changes, even if the
body is renamed — *Reload from disk* follows the path. When another body already
owns a name, the body key's first 8 characters are added: `<Project> - <Body>
(<key>).<fmt>`. A body linked before the folder changed gets a new file in the
new folder at its next Update. The bookkeeping stays in Orca's data directory:

```
takumion/
  pairings.json      token hashes, origins, times
  links.json         doc → body → path, format, lastSent
  location.json      the chosen folder
```

## Versioning

`protocol` in `hello` is the integer version. A change that an older client could
misread bumps it; adding an optional field does not. A client that sees a higher
major than it knows says "update Takumion" instead of guessing.
