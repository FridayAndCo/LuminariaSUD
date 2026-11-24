# LuminariaSUD

A small, data-driven fantasy RPG you can play in a terminal or a browser —
and fork without fear. The rules run on a **deterministic engine**; a local
LLM provides the voice: narration, NPC conversation, and improvised solutions.

You are the Protector of Luminaria, a city of drifting memories and a dimming
lamp. A town hub (temple, shops, tavern, eccentric citizens with errands for
you) sits beside a dungeon of procedural floors. Explore, fight, trade, take
quests, and report your deeds.

---

## Just here to play it? Start here.

**You need:** Python 3.10+, and any OpenAI-compatible LLM endpoint running
somewhere reachable — koboldcpp, llama.cpp server, LM Studio, or a hosted API.
The LLM is part of the *intended* experience (it voices the citizens and
narrates your deeds), but the game still plays without one: movement, combat,
loot, quests and shops are all resolved by the engine — only prose and free
conversation go flat. If `config.json` still has the placeholder URL, the game
opens by telling you that narration and improvise are unavailable — everything
else plays as-is.

```bash
git clone https://github.com/FridayAndCo/LuminariaSUD.git
cd LuminariaSUD
```

Edit `config.json` — point it at your model:

```json
{
  "api_url": "http://192.168.1.50:5001/v1",
  "model": "koboldcpp/YourModelName"
}
```

Then run it. **Terminal mode needs no third-party packages at all** — plain
Python is enough:

```bash
python3 luminaria.py     # or `python luminaria.py`, depending on your OS/environment
```

(`rich` adds color and panels to the terminal if you already have it installed;
the game plays fine without it.)

Web mode needs `fastapi` + `uvicorn`. The easiest way to get those is
[uv](https://docs.astral.sh/uv/) — it creates its own virtualenv, installs the
dependencies, and launches, with no PEP 668 headache on modern Debian/Ubuntu:

```bash
uv run python api.py --port 8090    # -> browse http://127.0.0.1:8090
```

Running terminal mode *through* uv works too, and is how you opt in to the
optional color extra:

```bash
uv run python luminaria.py              # plain (no extras)
uv run --extra rich python luminaria.py # with rich color
```

Prefer plain pip? `python3 -m venv .venv && source .venv/bin/activate &&
pip install -r requirements.txt` (or `pip install -e ".[rich]"` for the
color extra).

Saves live in `state/` and start empty on a fresh clone — the game creates
your character on first run.

### The commands you actually need

| | |
|---|---|
| `n` `s` `e` `w` `u` `d` | move (north, south, …, up, down) |
| `look` / `search` | see the room again / hunt for traps and hidden things |
| `talk` | free-flowing conversation with an NPC present (the LLM voices them) |
| `quest` | your current task — `quest accept` / `quest decline` / `quest abandon` |
| `inv` / `equip` / `use` | inventory is forgiving: *"equip torch"* finds *"Torches (6)"* |
| `shop` / `buy` / `sell` | merchants are in the town (and some talk to you while selling) |
| `cast` | mix reagents into spells from what you carry |
| `sleep` | bedroll in the dungeon, or the inn — restores HP and insight |
| `improvise` (or `i`) | try *anything else* — the GM resolves it (costs 1 insight) |
| `status` / `time` / `roll` | character panel / day & hour / ad-hoc die roll |
| `narrate` | toggle LLM prose on/off (**on by default**) |
| `save` / `help` / `quit` / `reset` | exactly what they say |

Anything the parser doesn't recognise is offered as an *improvise* — you're
asked `[Y/n]` before an insight charge is spent. Combat auto-starts when
something blocks your way: `attack`, `flee`, or `parley` your way out.

---

## How the game works (a minute of theory)

- **95% deterministic.** The engine (`engine.py`) resolves movement, combat,
  traps, loot, time, and quests. Same seed, same world. You can audit it,
  mod it, or diff it — no mystery dice.
- **The LLM is a bounded storyteller, not the referee.** It restyles outcomes
  as prose, voices conversations, and inside weird rooms it may *escalate*
  into a small tool-calling loop — but it can only call existing engine
  tools, and each escalation costs an **insight charge** (restored by sleep).
  The engine stays authoritative.
- **Quests.** Citizens offer tasks in conversation — fetch, gather, kill-X,
  or hunt one tough thing. Completing one is **+5 Virtue** and coin; one
  active quest at a time; abandoning costs −5 (but never drags negative
  virtue lower — Anti-Shrine runs are safe). Some quests turn in to a
  *different* NPC than the one who gave them.
- **Virtue & confer.** Deeds are tallied; report to Lord Raika at the castle
  (`confer`) for a capped virtue boost, once per sleep.
- **Time matters.** Day phases, sleep, inn stays, and quest offers keyed to
  the day — the town changes with the clock.

---

## Build on it: content without code

Almost everything about the world lives in `data/*.json`. Adding content is
usually *adding a key to a file*, not editing Python:

| Want to add… | Edit… | What it looks like |
|---|---|---|
| **A room / area** | `data/town_map.json` | Rooms keyed by `"x_y_z"` with a `name`, `description`, and `exits` pointing at other keys. Add a key, stitch two exits together, done. (Town rooms are hand-authored here; dungeon floors are generated and persisted in `state/world_map.json`.) |
| **An NPC** | `data/npcs.json` | Name, description, `traits` (voice), `hints` (what they know), optional `shop`. Keyed by the room they stand in. |
| **A monster** | `data/bestiary.json` + `data/encounter_tables.json` | `{"ac": 15, "hp": 30, "atk": 6, "special": ["petrify"]}`, then put its name on a floor's encounter table. |
| **An item** | `data/items_ledger.json` + `data/shop_inventory.json` | Category, price, rarity — then list it in whichever shop should stock it. |
| **A quest** | `data/quests.json` | A template: giver (NPC name or `"any"`), archetype (`fetch`/`gather`/`kill`/`hunt`), objective, turn-in, gold. Settings control virtue reward, abandon penalty, and re-offer cooldown. |
| **Flavor** | `discoveries.json`, `traps.json`, `obstacles.json`, `floor_themes.json`, `spells.json`, `narrative.json`, `virtue.json` | Weird rooms, hazards, dungeon atmospheres, spell recipes, prose fragments, virtue rules. |

Balance tweaks are JSON edits. The `state/` folder holds your save (gitignored)
— delete it for a clean slate.

## Architecture (go deeper)

The repo is deliberately shaped so the game can be lifted into other apps:

```
engine.py     deterministic rules engine (stdlib only; reads data/, owns state/ saves)
luminaria.py  CLI game layer: Game.process_command(raw) + Game.snapshot()
io_layer.py    the seam: TerminalIO (classic REPL) or QueueIO (API-served)
ui.py           all output routes through ui.SINK — swap the sink, keep the game
api.py        FastAPI actor server: one Game, one worker thread, HTTP endpoints
tools.py      the bounded tool list the LLM is allowed to call
llm.py        OpenAI-compatible client
data/*.json   the entire world
```

- **`Game.process_command(raw)`** — drive the whole game one command-line at
  a time from *anything*: a REPL, an HTTP request, a test harness, a Discord
  bot.
- **`Game.snapshot()`** — a JSON view of everything a UI needs:
  room (with exits tagged `exit/door/obstacle/stairs` and hotspots tagged
  `npc/monster/container/trap/corpse/shrine`), player stats, inventory,
  position, insight, quest state, and pre-rendered HUD text. Any frontend can
  paint this.
- **`api.py` endpoints** — `GET /api/state`, `GET /api/events?cursor=N`,
  `POST /api/command {text}` (409 if busy), `POST /api/answer {text}` for
  blocking prompts (combat, menus, conversation), `POST /api/reset`. The
  blocking conversation/combat loops survive HTTP: a command can pause on a
  prompt, stream new lines to the client, then finish when answered.

## Roadmap

- **Phase 1 — structured command API** ✅
- **Phase 2 — web shell** ✅ *(shipped with v0.2.0: compass, structured HUD,
  inventory action buttons, message log, command box, quest offer buttons)*
- **Phase 3 — hotspots as click targets** ✅ *(arrived early with 2c — chips
  fire talk / attack / open / pray / get / disarm / search)*
- **Phase 4 — art assets** (cached room imagery, item icons, a paper doll).
- **Phase 5 — generated imagery** (on-demand, seeded for consistency).

(A dedicated mobile/touch layout is deliberately out of scope for now — a
fork is welcome to own it.)

**Content horizons** (we intend to keep building, and so can you): many more
NPCs with distinct voices, dynamic NPC schedules (who's where, when), expanded
town districts, an overworld with more towns beyond Luminaria, and more
quests, monsters, and weird rooms all along the way.

### Magic system (sub-phase 2d, in progress)

The goal: every spell has a **real mechanical effect** that moves the game,
instead of leaning on the LLM to invent outcomes. Magic is deliberately
powerful in Luminaria — that's why it's locked behind a complex reagent
system. The engine resolves the dice, the state, and the consequences; the LLM
only narrates.

**Damage formula:** a damage spell deals `Xd6 + X` where `X = INT - 10`
(no halving). Low-level casters can't use the big spells — a L3 Fireball at
INT 10 is a fizzle.

**Work is landing in batches**, each pushable on its own:

1. **Batch 1 — damage spells + buff infrastructure** *(this push)*: the
   buff system (durations that tick on the turn loop, feeding AC / attacks /
   movement), a shared monster-save helper, and the five damage spells —
   **Fireball**, **Lightning Bolt** (d20 to hit), **Wall of Fire** (damage
   over time), **Disintegrate** (no corpse), and **Kill** (instant).
2. **Batch 2 — control spells**: Sleep, Hold Person, Web, Charm (Person /
   Monster), Confusion.
3. **Batch 3 — utility spells**: Lock, Knock, Levitate, Fly, Invisibility,
   Water Breathing, Passwall, Teleport, Locate Object, Read Language,
   Continuous Light, Remove Curse, Dispel Magic, Control Weather, Find Traps.
4. **Batch 4 — LLM-tool spells**: Divinate (revisit later), Polymorph,
   Commune with the Dead, Telekinesis, Speak with Plants / Animals, and the
   wildcard **Wish**.
5. **Batch 5 — counter-features**: `see_invisible` monster trait, the glyph
   feature, and Raise-Dead curses.

The full per-spell design (what each one does, whether it's mechanical, a
buff, or an LLM tool) is tracked in the project notes. If you're forking,
batches 1–3 are pure engine work you can read and extend; batch 4 is where
the LLM-tool seam lives.

Everyone is free to build on this however they wish — it's GPL-3.0. We'll
keep adding to it; consider this a world with room in it.

## License

**GNU General Public License v3.0** (GPL-3.0) — see [LICENSE](LICENSE).
Free to use, study, share, and adapt, on copyleft terms.
