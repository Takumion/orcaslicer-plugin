# Takumion Link for OrcaSlicer

An [OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer) 2.5+ plugin that puts
bodies sent from the Takumion CAD app on Orca's plate, and keeps them linked:

- **Update linked** — each body has one fixed file. The first send puts it on
  the plate; later sends overwrite the same file, and Orca's
  *right-click › Reload from disk* brings the new geometry in while keeping
  the object's placement. A body that was deleted from the plate is put back.
- **Add copy** — a new, time-stamped file on the plate, never linked.

The plugin adds a **Takumion** tab to Orca: link status, pairing, paired apps
(with *Revoke*), linked bodies with a *Reload from disk* badge, a one-time
3MF setup card (shown when a 3MF did not land), and *Open Takumion*.

It contains no Takumion code. Takumion talks to it over a small documented
loopback protocol: [`docs/slicer-link-protocol.md`](docs/slicer-link-protocol.md).

## Install

1. Download `takumion_link_any.py` from the releases (or build it, below).
2. In OrcaSlicer: install it as a local plugin from the Plugins dialog.
   Orca keeps it at `{data_dir}/orca_plugins/takumion_link_any.py/`.
3. On first load Orca asks once for the plugin's permissions:
   - **listen on `127.0.0.1` ports 47390–47394** — the local link Takumion
     connects to (loopback only; nothing is reachable from the network);
   - **the session bus socket** (Linux) — how a file is handed to Orca's own
     loader, the same way a second Orca window would hand it over;
   - **threads** — the link server runs beside Orca's UI.
4. Open the **Takumion** tab, click **Pair an app**, and type the 6-digit code
   into Takumion (*Send to OrcaSlicer…* or *Settings › Slicer*). The code
   lives 120 s and allows 5 tries.

*Open Takumion* asks once more, the first time, to run the system's URL opener.

### STL by default; 3MF needs one Orca setting

Takumion sends STL unless you pick 3MF. STL is always a plain import.

For 3MF, set **Preferences › Load behavior → Load geometry only** once. With
Orca's default load behaviour a 3MF either opens a "project or geometry"
question or — on an empty plate — opens as the **project**, so a later *Save*
would write the whole project into the linked file. A plugin cannot read or
change that preference, so the plugin watches instead: a 3MF that does not
appear on the plate within 5 s, or that became the project, brings up the
setup card in the Takumion tab (and Takumion shows the same hint). A link
file opened as the project is dropped from the links; the next send writes a
fresh file.

## How it works

| Piece | File |
|---|---|
| Plugin entry: manifest, permissions, the tab capability | `takumion_link.py` |
| Loopback HTTP transport (Host check, pairing tokens, CORS) | `link/server.py` |
| `hello` / `send` / `links`, 3MF guards, reload badges | `link/service.py` |
| Pairing codes and token hashes (`pairings.json`) | `link/pairing.py` |
| Body ↔ file links (`links.json`), file layout | `link/store.py` |
| Plate copy taken on Orca's UI thread | `link/plate.py` |
| Hand-off to Orca's single-instance loader, per OS | `link/handoff*.py`, `link/dbus.py` |
| `window.orca` bridge for Takumion running in the tab | `link/bridge.py` |
| The tab: state and buttons | `link/tab.py`, `frontend/` |

Orca's plugin API cannot add objects to the plate, so the plugin uses the
path Orca already has for files opened from outside: its single-instance
message. One file per message (several files in one message become one
object with several parts).

| Platform | Hand-off | Status |
|---|---|---|
| Linux (Flatpak and native) | D-Bus `AnotherInstance`, followed by a round trip so the Flatpak bus proxy forwards it | checked |
| Windows | `WM_COPYDATA` to Orca's main window | written, not yet checked |
| macOS | distributed notification `OtherOrcaSlicerInstanceMessage<hash>` | written, not yet checked |

Files live under Orca's data folder:

```
takumion/
  pairings.json   links.json   state.json
  links/<docKey>/<bodyKey>/<name>.<fmt>
  copies/<docKey>/<name>-<YYYYMMDD-HHMMSS>.<fmt>
```

## Develop

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                      # pytest into .venv
uv run pytest                # the suite, with our own fake `orca` module
uv run python dev/serve.py   # the tab in a browser at http://127.0.0.1:8766/
uv run python packaging/bundle.py   # dist/takumion_link_any.py (size-checked, < 1 MB)
```

`dev/serve.py` runs the real link server on the first free protocol port
against a simulated plate, so Takumion can pair with and send to it without
Orca (`--orca dialog` simulates the 3MF question, `--orca project` a 3MF
opened as the project).

## Licence

AGPL-3.0-only — see [`LICENSE`](LICENSE).
