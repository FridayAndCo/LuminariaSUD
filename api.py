#!/usr/bin/env python3

# LuminariaSUD - a deterministic dungeon crawler with a web/API shell.
# Copyright (C) 2026 Apollo (FridayAndCo)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""api.py - web API for LuminariaSUD (single + multiplayer "Caller" model).

The game is a single-threaded "actor": one Game instance is driven by one
worker thread through Game.process_command(). A web (or any HTTP) client talks
JSON to FastAPI endpoints and never touches stdin.

Because the game's combat/conversation loops block on input(), the worker can
pause mid-command waiting on a prompt; the client streams new lines via
/events?cursor=N and answers the prompt via /answer - so one logical command
may span many HTTP round-trips without deadlocking.

Multiplayer ("Caller" / satellite model, Option B): the single-player
character does NOT carry over. There are two modes with separate saves:

  * single - the classic one-character run (state/player_state.json).
  * multi  - a fixed 4-roster (P1-P4) under state/mp/. P1 is the Caller (the
             actor). P2..P(N-1) are satellites: co-located at the Caller's
             position, engine-affecting combatants. The MP world persists
             across sessions (state/mp/world_map.json) and resumes.

Satellite actions do NOT go through the game worker (which is busy mid-turn):
they are small, thread-safe calls - lock-in (buffered, drained into the next
combat turn) and a chat bus (a thread-safe append to the event log). The host
browser is the Caller session; non-host browsers claim a satellite slot and
their lock-in/chat are tagged with it.

Run:  python3 api.py            (http://127.0.0.1:8090)
      python3 api.py --port 9000 --no-narrate
"""

import argparse
import json
import os
import threading

import engine as E
import io_layer
import ui
import luminaria as L
try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, JSONResponse
    from pydantic import BaseModel
except ImportError as _e:  # friendly hint instead of a raw traceback
    import sys
    sys.stderr.write(
        "\n[LuminariaSUD] The web server needs a few Python packages that\n"
        "aren't installed yet ({}).\n\n"
        "    pip install -r requirements.txt\n\n"
        "Tip: terminal mode needs none of these - just run\n"
        "    python3 luminaria.py\n\n".format(_e)
    )
    sys.exit(1)

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
STATE_DIR = os.path.join(BASE, "state")
LAUNCH_FILE = os.path.join(STATE_DIR, "launch.json")


# ---- launch mode persistence (so a restart resumes the same mode) ----
def _load_launch():
    cfg = {"mode": "single", "party_size": E.MP_MAX}
    if os.path.exists(LAUNCH_FILE):
        try:
            with open(LAUNCH_FILE) as f:
                cfg.update(json.load(f))
        except json.JSONDecodeError:
            pass
    cfg["mode"] = "multi" if cfg.get("mode") == "multi" else "single"
    cfg["party_size"] = max(1, min(E.MP_MAX, int(cfg.get("party_size", E.MP_MAX))))
    return cfg


def _save_launch(mode, party_size):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LAUNCH_FILE, "w") as f:
        json.dump({"mode": mode, "party_size": party_size}, f, indent=4)


# ---- game builder (mode is fixed before the engine is constructed) ----
def _build_game(mode, party_size):
    E.set_mode(mode)
    cfg = L.load_cfg()
    game = L.Game(cfg)
    if mode == "multi":
        game.eng.load_satellites(party_size)
        E.save_mp_config({"mode": "multi", "party_size": party_size})
    return game


# ---- shared game state (the actor) ----
QIO = io_layer.QueueIO()
ui.set_sink(QIO)

_GAME = {"game": None, "mode": "single", "party_size": E.MP_MAX}
_launch = _load_launch()
_GAME["mode"] = _launch["mode"]
_GAME["party_size"] = _launch["party_size"]
_GAME["game"] = _build_game(_launch["mode"], _launch["party_size"])
_notice = _GAME["game"].narration_notice()
if _notice:
    QIO.emit("warn", _notice)


def _current_game():
    return _GAME["game"]


_lock = threading.RLock()        # guards GAME mutation + snapshot reads
_gate = threading.Lock()         # guards the busy flag only (never held during a prompt)
_busy = False
_last_snapshot = None
# cooperative slot registry: slot -> client id (host browser claims P1).
_claims = {}


def _set_busy(v):
    global _busy
    _busy = v


def _snapshot_safe():
    """Snapshot only when the worker isn't mid-command (avoids torn reads)."""
    global _last_snapshot
    if _lock.acquire(blocking=False):
        try:
            _last_snapshot = _current_game().snapshot()
        finally:
            _lock.release()
    return _last_snapshot


# ---- worker thread ----
def _worker():
    global _busy
    while True:
        text = _jobs.get()
        _set_busy(True)
        game = _current_game()  # a command runs to completion on its own game
        try:
            with _lock:
                game.process_command(text)
        except Exception as e:  # never let the actor die on a bad command
            QIO.emit("bad", f"(api error: {e})")
        finally:
            _set_busy(False)
        _jobs.task_done()


import queue  # noqa: E402
_jobs = queue.Queue()
_thread = threading.Thread(target=_worker, daemon=True)
_thread.start()


# ---- models ----
class Cmd(BaseModel):
    text: str = ""


class Ans(BaseModel):
    text: str = ""


class ModeSel(BaseModel):
    mode: str = "single"          # "single" | "multi"
    party_size: int = E.MP_MAX    # 1..4 (multi only)


class LockinReq(BaseModel):
    slot: str = ""
    action: str = "pass"          # "fight" | "pass" | "none"


class ChatReq(BaseModel):
    slot: str = ""
    text: str = ""


class TransferReq(BaseModel):
    slot: str = ""
    item: str = "gold"
    qty: int = 1
    direction: str = "to"         # "to" (Caller -> sat) | "from" (sat -> Caller)


class ClaimReq(BaseModel):
    slot: str = ""
    client_id: str = ""


# ---- app ----
app = FastAPI(title="LuminariaSUD API", version="0.3.10")


@app.get("/", response_class=HTMLResponse)
def index(legacy: bool = False):
    # The Phase 1 test harness stays reachable at /?legacy=1.
    fname = "legacy.html" if legacy and os.path.exists(os.path.join(STATIC, "legacy.html")) else "index.html"
    path = os.path.join(STATIC, fname)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    return "<h1>LuminariaSUD API</h1><p>See /docs. POST /api/command {text}.</p>"


@app.get("/api/state")
def state():
    return JSONResponse({
        "snapshot": _snapshot_safe(),
        "busy": _busy,
        "awaiting_prompt": QIO.awaiting_prompt,
        "prompt": QIO._prompt_text if QIO.awaiting_prompt else None,
        "mode": _GAME["mode"],
        "party_size": _GAME["party_size"],
    })


@app.get("/api/events")
def events(cursor: int = 0):
    evs, new_cursor = QIO.poll(cursor)
    return JSONResponse({
        "events": evs,
        "cursor": new_cursor,
        "busy": _busy,
        "awaiting_prompt": QIO.awaiting_prompt,
        "prompt": QIO._prompt_text if QIO.awaiting_prompt else None,
        # refresh panels once the command has finished
        "snapshot": None if _busy else _snapshot_safe(),
    })


@app.post("/api/command")
def command(cmd: Cmd):
    text = (cmd.text or "").strip()
    if not text:
        raise HTTPException(400, "empty command")
    with _gate:
        if _busy:
            raise HTTPException(409, "busy - a command is already running")
        _set_busy(True)
    _jobs.put(text)
    return JSONResponse({"started": True, "text": text})


@app.post("/api/answer")
def answer(ans: Ans):
    if not QIO.awaiting_prompt:
        raise HTTPException(409, "no prompt is waiting")
    QIO.answer_prompt((ans.text or "").strip())
    return JSONResponse({"answered": True})


@app.post("/api/reset")
def reset():
    """Abandon the run and reset to defaults (bypasses the CLI confirm prompt).
    In multi mode the satellites are reset to fresh characters too."""
    with _gate:
        if _busy:
            raise HTTPException(409, "busy")
        _set_busy(True)
    try:
        with _lock:
            game = _current_game()
            game.eng.reset_game()
            game.insight = game.insight_max
            game.last_narration = None
            QIO.emit("say", "The world dissolves and reknits itself at the Temple of Radiance.")
            snap = game.snapshot()
    finally:
        _set_busy(False)
    return JSONResponse({"reset": True, "snapshot": snap})


# ---- multiplayer: mode / party selection ----
@app.post("/api/mode")
def set_mode(m: ModeSel):
    """Launch a new session in the chosen mode. 'single' = the classic run;
    'multi' = the fixed 4-roster with a party of 1..4 (P1 always the Caller).
    Rebuilds the actor and persists the launch choice (MP world resumes)."""
    mode = "multi" if (m.mode or "").lower() == "multi" else "single"
    party_size = max(1, min(E.MP_MAX, int(m.party_size or E.MP_MAX)))
    with _gate:
        if _busy:
            raise HTTPException(409, "busy - wait for the current command")
        _set_busy(True)
    try:
        with _lock:
            game = _build_game(mode, party_size)
            _GAME["game"] = game
            _GAME["mode"] = mode
            _GAME["party_size"] = party_size
            _save_launch(mode, party_size)
            if mode == "multi":
                if party_size >= 2:
                    QIO.emit("say", f"Multiplayer session: party of {party_size} "
                                    f"(P1 is the Caller; P2..P{party_size-1} are satellites).")
                else:
                    QIO.emit("say", "Multiplayer session: you are the Caller alone "
                                    "(no satellites this run).")
            else:
                QIO.emit("say", "Single-player session: the classic one-character run.")
            snap = game.snapshot()
    finally:
        _set_busy(False)
    return JSONResponse({"mode": mode, "party_size": party_size, "snapshot": snap})


# ---- multiplayer: satellite lock-in (thread-safe, NOT through the worker) ----
@app.post("/api/lockin")
def lockin(lk: LockinReq):
    """A satellite locks in its next-turn action ('fight'/'pass'). The action
    is buffered and drained into the next combat turn; no input == pass, so
    the game never waits. The Caller sees who is ready via /api/roster."""
    slot = (lk.slot or "").upper()
    if slot not in E.MP_SLOTS:
        raise HTTPException(400, f"{slot} is not a valid slot")
    with _lock:
        ok, msg = _current_game().sat_set_lockin(slot, lk.action)
    QIO.emit("say" if ok else "warn", f"[{slot}] {msg}")
    return JSONResponse({"ok": ok, "message": msg})


# ---- multiplayer: chat bus (thread-safe append, NOT through the worker) ----
@app.post("/api/chat")
def chat(c: ChatReq):
    """A satellite (or the Caller) posts a line to the shared feed. This is a
    plain append to the event log - the LLM/engine never reads it, it is the
    players talking to each other."""
    slot = (c.slot or "").upper()
    text = (c.text or "").strip()
    if not text:
        raise HTTPException(400, "empty message")
    if slot not in E.MP_SLOTS:
        raise HTTPException(400, f"{slot} is not a valid slot")
    QIO.emit("say", f"{slot}: {text}")
    return JSONResponse({"ok": True})


# ---- multiplayer: transfer gear/gold (Caller-only op, thread-safe) ----
@app.post("/api/transfer")
def transfer(t: TransferReq):
    """Move an item or 'gold' between the Caller and a satellite. Only the
    Caller holds keys/inn-key; a satellite carries gear + gold."""
    slot = (t.slot or "").upper()
    if slot not in E.MP_SLOTS:
        raise HTTPException(400, f"{slot} is not a valid slot")
    direction = "from" if (t.direction or "").lower() == "from" else "to"
    with _lock:
        res = _current_game().eng.transfer(slot, t.item, t.qty, direction)
    QIO.emit("say" if res.get("success") else "warn", res.get("message", str(res)))
    return JSONResponse(res)


# ---- multiplayer: roster + cooperative slot claims ----
@app.get("/api/roster")
def roster():
    """The party roster (C4 Caller visibility) plus which slots a browser has
    claimed. The host browser is the Caller (P1); others claim P2..P4."""
    with _lock:
        rows = _current_game().sat_roster()
    return JSONResponse({"party": rows, "mode": _GAME["mode"],
                         "party_size": _GAME["party_size"], "claims": dict(_claims)})


@app.post("/api/claim")
def claim(req: ClaimReq):
    slot = (req.slot or "").upper()
    if slot not in E.MP_SLOTS:
        raise HTTPException(400, f"{slot} is not a valid slot")
    cid = (req.client_id or "").strip()
    if not cid:
        raise HTTPException(400, "client_id required")
    _claims[slot] = cid
    return JSONResponse({"claimed": True, "slot": slot, "client_id": cid})


@app.post("/api/release")
def release(req: ClaimReq):
    slot = (req.slot or "").upper()
    cid = (req.client_id or "").strip()
    if _claims.get(slot) == cid:
        _claims.pop(slot, None)
    return JSONResponse({"released": True, "slot": slot})


def main():
    ap = argparse.ArgumentParser(description="LuminariaSUD web API (single + multiplayer)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--narrate", dest="narrate", action="store_true", default=None,
                    help="force LLM prose ON (default: from config.json - ON)")
    ap.add_argument("--no-narrate", dest="narrate", action="store_false",
                    help="force LLM prose OFF")
    args = ap.parse_args()
    if args.narrate is not None:
        _current_game().narrate_on = args.narrate
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
