# Slicer link protocol — v1

How Takumion (web or desktop) sends bodies to OrcaSlicer through the **Takumion
Link** Orca plugin. This page is the whole contract between the two: the plugin
repository carries a verbatim copy, and neither side relies on anything not
written here.

- **Plugin:** listens on loopback, writes the files, puts them on Orca's plate,
  tracks which plate objects are linked to which bodies.
- **Takumion:** exports meshes, pairs once, sends.
- **Design and decisions:** [`plans/orca-slicer-link.md`](../plans/orca-slicer-link.md).

Two transports carry the same messages:

| Transport | Used by | Pairing |
|---|---|---|
| HTTP/JSON on `127.0.0.1` | Takumion in a browser or the desktop app | Required |
| `window.orca` bridge | Takumion running inside Orca's plugin tab | Not needed |

## Discovery

The plugin binds **`127.0.0.1` only**, on the first free port of
**47390, 47391, 47392, 47393, 47394**. A client probes them in that order with
`GET /v1/hello` and uses the first answer whose `app` is `"takumion-orca-link"`.
A client remembers the last port that answered and tries it first.

No answer on any port means Orca is not running, or runs without the plugin.
The desktop app may then start Orca (no file argument) and probe again for up to
30 s; the web app shows how to start it.

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
- **Tokens** are 32 random bytes, base64url. The plugin stores only their SHA-256,
  with origin, client name, created and last-used times. The user can revoke any
  pairing from the Orca tab.
- **CORS.** `hello` and `pair` answer any origin (they reveal nothing and need a
  code). Every other endpoint echoes `Access-Control-Allow-Origin` only for a
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
- **Limits.** Request body ≤ 384 MB (`413` above). Keys are
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
  "paired": false,
  "setup": {"loadGeometryOnly": "unknown"}
}
```

- `paired` is `true` when the request carries a valid token for its origin.
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

Methods: `hello`, `send`, `links`. No pairing: the plugin only ever navigates its
own tab to the Takumion app.

**Getting there and back.** The plugin's tab has an *Open Takumion here* button
that navigates the tab to `<takumionUrl>?host=orca` (`takumionUrl` is the plugin's
setting: `https://app.takumion.com/` by default, a local dev server while
developing). Orca injects `window.orca` into the page after the navigation.
Over the bridge, `hello`'s result carries one more, optional field:

```json
{"app": "takumion-orca-link", "protocol": 1, "…": "…", "tab": {"url": "http://127.0.0.1:47390/tab"}}
```

`GET /tab` on the loopback server serves the plugin's own tab page (static HTML,
`Host`-checked like every endpoint, no data in it; its state comes over
`window.orca`). The editor's "back to Takumion Link" navigates there. `tab` is
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

Under Orca's data directory (the one place a plugin may write without asking):

```
takumion/
  pairings.json                        token hashes, origins, times
  links.json                           doc → body → path, format, lastSent
  links/<docKey>/<bodyKey>/<name>.<fmt>   update mode: path fixed at first send
  copies/<docKey>/<name>-<YYYYMMDD-HHMMSS>.<fmt>   add mode
```

A body's link path is decided at its first send and never changes, even if the
body is renamed — *Reload from disk* follows the path.

## Versioning

`protocol` in `hello` is the integer version. A change that an older client could
misread bumps it; adding an optional field does not. A client that sees a higher
major than it knows says "update Takumion" instead of guessing.
