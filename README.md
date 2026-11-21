# LuminariaSUD — Phase 1: Structured command API

A fork of the Luminaria dungeon crawler with the engine **unchanged**, refactored
so the game can be driven from outside the terminal. This is Phase 1 of the
roadmap (web shell → hotspots → art → generated imagery).

## Luminaria (terminal port)

A standalone terminal RPG port of the `rpgmodule` webui app.

### Architecture

- **95% deterministic**: the engine (engine.py) resolves movement, combat, traps,
  loot, etc. One LLM call with `tools=[]` restyles the outcome as prose.
- **5% improvise**: puzzle/weird rooms escalate into a bounded tool-calling
  sub-loop (max 5 steps). The LLM may only call existing engine tools, so the
  engine stays authoritative. Each escalation costs an "insight charge"
  (restored by sleeping).
- **Data-driven**: all game content lives in `data/*.json` (npcs, traps,
  obstacles, spells, bestiary, encounter tables, items ledger, shop inventory,
  town map, narrative). Balance tweaks = JSON edits, no code changes.

## What changed

The engine (`engine.py`) was copied verbatim — same rules, same
`state/*.json` save format. The only edits are to the game/IO layer:

- **`io_layer.py` (new)** — a swappable output/input sink. The single seam that
  lets identical logic run under the terminal or the API.
  - `TerminalIO` → the classic `print` / `input` REPL.
  - `QueueIO` → buffers every line into a cursor-pollable log; blocking prompts
    park on a queue until a client answers them.

- **`ui.py`** — all HUD/room/text output now routes through the sink
  (`ui.SINK`). Rich panels still render for the terminal; the API always gets
  clean, tag-free text. Added `render_hud()` (returns a string) and `_is_api()`.

- **`luminaria.py`**
  - Every `input()` → `ui.prompt()` (routes through the sink).
  - The command dispatch + redraw tail were lifted out of the REPL `run()` loop
    into **`Game.process_command(raw)`** → `{"quit": bool}`. `run()` is now a
    thin adapter over it.
  - New **`Game.snapshot()`** → a JSON-serialisable view:
    `{ room: {id,name,description,exits,hotspots,…}, player: {hp,stats,
    inventory,position,…}, insight, hud_text, … }`. Exits carry `kind`
    (exit/door/obstacle/stairs) and hotspots (npc/monster/container/trap/
    corpse/shrine) are pre-tagged for Phase 3 click targets.

- **`api.py` (new)** — a FastAPI **actor server**: one `Game` instance driven by
  one worker thread. Endpoints:

  | Method | Path | Purpose |
  |--------|------|---------|
  | GET  | `/`                      | minimal test page (static/index.html) |
  | GET  | `/api/state`             | snapshot + busy/awaiting flags |
  | GET  | `/api/events?cursor=N`   | stream new lines since cursor N |
  | POST | `/api/command` `{text}`  | run one command line (409 if busy) |
  | POST | `/api/answer`  `{text}`  | answer a pending prompt (combat/menu/talk) |
  | POST | `/api/reset`             | reset the run (no confirm) |

  The blocking combat/conversation sub-loops work across HTTP: a command can
  pause on a prompt, the client streams new lines and answers it, and the same
  command then finishes. A second command during a busy command returns **409**.

## Quickstart

Clone, set your API endpoint, install, and play:

```bash
git clone https://github.com/FridayAndCo/LuminariaSUD.git
cd LuminariaSUD

# 1. Point the game at your LLM endpoint by editing config.json:
#      "api_url" -> e.g. http://127.0.0.1:5001/v1
#      "model"   -> your model name

# 2. Install the web dependencies (terminal mode needs none of these):
pip install -r requirements.txt

# 3a. Terminal mode (stdlib only, no install required):
python3 luminaria.py

# 3b. Web mode (needs the deps above; narration off by default, add --narrate):
python3 api.py --port 8090
#     then browse http://127.0.0.1:8090   (or /docs for OpenAPI)
```

> Web mode needs `fastapi` + `uvicorn` (pydantic comes with FastAPI). If you
> skip the install, `api.py` prints a friendly reminder rather than a traceback.
> `rich` is optional and only adds colour/panels to terminal mode.

## Verified

- Terminal REPL regression: help / look / inv / roll / narrate / quit all work.
- API: state, streaming events, move, and the **blocking-prompt round-trip**
  (parser-miss confirm → answer `n`) all pass; concurrent command → 409.
- LLM path runs safely inside the worker thread (~9s call, prose rendered into
  the room panel).
- Save format untouched — same `state/player_state.json` + `state/world_map.json`.

## Not yet (later phases)

- Phase 2: full web shell layout (clickable exits, separate panels). The current
  `static/index.html` is a functional test harness, not the final UI.
- Phase 3: hotspots as live click targets (data already exposed in `snapshot`).
- Phases 4–5: art assets and generated imagery.

## License

Released under the **GNU General Public License v3.0** (GPL-3.0) — see [LICENSE](LICENSE).
Free to use, study, share, and adapt, on copyleft terms.
