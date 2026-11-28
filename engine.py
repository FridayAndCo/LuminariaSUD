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

"""
Luminaria - terminal RPG engine.
Ported verbatim from user_modules/rpgmodule/module.py with the Lumara harness
dependency (core / channels.webui / cairosvg) removed. All content tables are
loaded from data/*.json so balance & content tweaks never touch code.
State (player + world) persists to state/*.json.
"""
import json
import random
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
STATE_DIR = os.path.join(BASE_DIR, "state")
os.makedirs(STATE_DIR, exist_ok=True)

PLAYER_FILE = os.path.join(STATE_DIR, "player_state.json")
WORLD_FILE = os.path.join(STATE_DIR, "world_map.json")

# ---- play modes: "single" (one character, the classic run) and "multi"
# (a fixed roster of up to 4 continuously-saved characters: P1 = the Caller,
# P2..P4 = satellites, all co-located at the Caller's position). The mode
# re-points the state files so the two experiences never share a save. ----
MODE = "single"
MP_STATE_DIR = os.path.join(STATE_DIR, "mp")
MP_MAX = 4
MP_SLOTS = ["P1", "P2", "P3", "P4"]
MP_CONFIG_FILE = os.path.join(MP_STATE_DIR, "config.json")
_single_player_file = os.path.join(STATE_DIR, "player_state.json")
_single_world_file = os.path.join(STATE_DIR, "world_map.json")
_mp_player_file = os.path.join(MP_STATE_DIR, "P1.json")
_mp_world_file = os.path.join(MP_STATE_DIR, "world_map.json")


def mp_slot_file(slot):
    return os.path.join(MP_STATE_DIR, slot + ".json")


def set_mode(mode):
    """Switch the active play mode and re-point the state files.

    Must be called before the GameEngine is built. "single" uses the classic
    one-character save; "multi" uses the fixed 4-roster under state/mp/.
    """
    global MODE, PLAYER_FILE, WORLD_FILE
    mode = (mode or "single").lower()
    if mode == "multi":
        os.makedirs(MP_STATE_DIR, exist_ok=True)
        MODE = "multi"
        PLAYER_FILE = _mp_player_file
        WORLD_FILE = _mp_world_file
    else:
        MODE = "single"
        PLAYER_FILE = _single_player_file
        WORLD_FILE = _single_world_file
    return MODE


def load_mp_config():
    """The multiplayer session config: {'mode','party_size'}. Defaults to a
    full party of 4 when absent."""
    cfg = {"mode": "multi", "party_size": MP_MAX}
    if os.path.exists(MP_CONFIG_FILE):
        try:
            with open(MP_CONFIG_FILE, "r") as f:
                cfg.update(json.load(f))
        except json.JSONDecodeError:
            pass
    cfg["party_size"] = max(1, min(MP_MAX, int(cfg.get("party_size", MP_MAX))))
    return cfg


def save_mp_config(cfg):
    os.makedirs(MP_STATE_DIR, exist_ok=True)
    with open(MP_CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=4)


def _load_json(name, default):
    path = os.path.join(DATA_DIR, name)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return default
    return default


# --------------------------------------------------------------------------- #
# NPC
# --------------------------------------------------------------------------- #
class NPC:
    def __init__(self, name, description, traits, hints=None, location_id=None, shop=None):
        self.name = name
        self.description = description
        self.traits = traits
        self.hints = hints or {}
        self.location_id = location_id
        # Optional key into shop_inventory.json for NPCs who sell wares - a
        # street merchant like Slippery Pete, whose stall lives in a shared
        # street room rather than a dedicated shop room of his own.
        self.shop = shop


# --------------------------------------------------------------------------- #
# Content tables (loaded from data/)
# --------------------------------------------------------------------------- #
_narrative = _load_json("narrative.json", {})
NARRATIVE_CONTEXT = _narrative.get("context", {})
ROOM_TYPE_DESCRIPTIONS = _narrative.get("room_type_descriptions", {})
DARKNESS_DESCRIPTION = _narrative.get(
    "darkness_description",
    "The room is consumed by absolute darkness.",
)

# --- Time of day: four phases, each lasting PHASE_TICKS satiety ticks ---
TIME_PHASES = ["Morning", "Day", "Evening", "Night"]
PHASE_TICKS = 25

# --- Food spoilage: perishables turn after N satiety ticks (the clock ages
# what the clock measures; preserved food never turns) ---
PERISHABLE_FOODS = {"Rations (meal in town)": 3, "Monster Meat": 3}
SPOILED_NAME = "Spoiled Food"
SPOILAGE_FLAVOR = {
    "Rations (meal in town)": "The warm smell from your pack has gone sour - your meal is now Spoiled Food.",
    "Monster Meat": "Raw meat does not keep in a pack full of steel and sweat - it is now Spoiled Food.",
}
EMERGENCE_FLAVOR = _narrative.get("emergence_flavor", {})

# --- Dungeon ways: an obstruction belongs to the doorway (the edge), never
# to the room, so a blocked door is only found from the side it stands on.
# Chances used when the generator classifies each adjacency edge.
CLOSED_DOOR_CHANCE = 0.20
LOCKED_DOOR_CHANCE = 0.10
OBSTACLE_EDGE_CHANCE = 0.30

# --- The inn's upper guest room: a locked stairway keyed to a consumable Inn
# Key (3 gp). The key is the whole transaction - sleeping in the room is free.
# An open door re-locks on its own after INN_LOCK_TICKS satiety ticks, or
# immediately if the guest sleeps and leaves. ---
INN_ROOM_ID = "-1_-1_1"
INN_BASE_ID = "-1_-1_0"
INN_KEY_ITEM = "Inn Key"
INN_LOCK_TICKS = 50

TRAP_DATA = _load_json("traps.json", {})


DEATH_MESSAGE = ("You have perished. The High Priestess sighs audibly, looking profoundly "
                 "bored as she pulls your soul back to the Temple of Radiance. 'Again?' she "
                 "mutters. 'Honestly, the Eternal part of your title is starting to feel like "
                 "a personal attack on my schedule.'")
OBSTACLE_DATA = _load_json("obstacles.json", {})
FLOOR_THEMES = _load_json("floor_themes.json", {})
DISCOVERY_DATA = _load_json("discoveries.json", {})
VIRTUE_DATA = _load_json("virtue.json", {})
SPELL_DATA = _load_json("spells.json", {})
MAGIC_DATA = _load_json("magic.json", {})

NPC_REGISTRY = {}
for _rid, _d in _load_json("npcs.json", {}).items():
    NPC_REGISTRY[_rid] = NPC(
        _d["name"], _d["description"], _d["traits"], _d.get("hints"), _d.get("location_id"),
        shop=_d.get("shop")
    )

# Reverse lookup for quest turn-in: NPC name -> room id.
NPC_ROOM_BY_NAME = {npc.name: rid for rid, npc in NPC_REGISTRY.items()}

# --- Quests ---------------------------------------------------------------- #
# Templates are static content (data/quests.json). The engine owns ALL quest
# state (player.data['quest']) and resolves every offer/accept/complete step
# deterministically, so quests survive reload and need no LLM to adjudicate.
_quests_doc = _load_json("quests.json", {})
QUEST_TEMPLATES = _quests_doc.get("quests", [])
QUEST_SETTINGS = {
    "virtue_reward": 5,
    "abandon_penalty": 5,
    "recent_cooldown_days": 3,
}
QUEST_SETTINGS.update(_quests_doc.get("settings", {}))

# Item category tables (populated by load_ledger)
REAGENT_DATA = {}
WEAPON_DATA = {}
RANGED_DATA = {}
ARMOR_DATA = {}
PROVISIONS_DATA = {}
GENERAL_DATA = {}
POUCH_DATA = {}
TRANSPORT_DATA = {}
KEY_DATA = {}
LOCKPICK_DATA = {}
READABLE_DATA = {}
LEDGER_DATA = {}
LEDGER = {}


def load_ledger():
    """(Re)load the items ledger JSON into the module-level category globals."""
    global REAGENT_DATA, WEAPON_DATA, RANGED_DATA, ARMOR_DATA, PROVISIONS_DATA, GENERAL_DATA
    global TRANSPORT_DATA, KEY_DATA, LOCKPICK_DATA, LEDGER_DATA, LEDGER, POUCH_DATA
    global READABLE_DATA
    ledger = _load_json("items_ledger.json", {})
    LEDGER = ledger
    REAGENT_DATA = ledger.get("reagents", {})
    WEAPON_DATA = ledger.get("weapons", {})
    RANGED_DATA = ledger.get("ranged_weapons", {})
    ARMOR_DATA = ledger.get("armor", {})
    PROVISIONS_DATA = ledger.get("provisions", {})
    GENERAL_DATA = ledger.get("general", {})
    POUCH_DATA = ledger.get("pouches", {})
    TRANSPORT_DATA = ledger.get("transport", {})
    KEY_DATA = ledger.get("keys", {})
    LOCKPICK_DATA = ledger.get("lockpicks", {})
    READABLE_DATA = ledger.get("readables", {})
    LEDGER_DATA.clear()
    for category in ledger.values():
        for item, info in category.items():
            LEDGER_DATA[item] = info["price"] if isinstance(info, dict) else info


load_ledger()


# --------------------------------------------------------------------------- #
# Loose item-name resolution
# --------------------------------------------------------------------------- #
# Players type fast ("equip torch", "unequip small wooden shield") and the LLM
# improvises whatever phrasing it likes, but inventory keys are the exact ledger
# names - often with quantity or spec noise attached ("Torches (6)",
# "Rope, 50 ft", "Axe, Battle"). Everything below normalizes both sides to one
# comparable key so matching ignores case, plurals and word order.

_ITEM_STOPWORDS = {"a", "an", "the", "some", "my", "item"}

# Multi-unit purchases: ledger key -> (unit name, units). add_item() collapses
# these into units so light/fuel math stays in one place.
BUNDLES = {
    "Torches (6)": ("Torch", 6),
    "Arrows (20)": ("Arrows", 20),
    "Bolts, Case of 30": ("Bolts", 30),
    "Sling Stones (20)": ("Sling Stones", 20),
    "Spikes, Iron (12)": ("Spikes, Iron", 12),
    "Stakes, Wooden (12)": ("Stakes, Wooden", 12),
}

# Things that burn down to nothing on their own timer rather than being worn.
# Dropped items are NOT deleted: they lie in the room for a few moves so the
# floor can be used as a free staging area (juggling gear must not cost
# satiety - that is the time budget). The clock is per-item, counted in moves,
# and only the room you ENTER decays - everything else waits.
DROP_FADE_MOVES = (4, 7)
DROP_DECAY = "drifts away, forgotten with the place"

LIGHT_FUEL_KEYS = {"torch": "Torch", "lantern": "Lantern"}

# British and colloquial spellings for the equipment slot names.
SLOT_ALIASES = {"armour": "armor", "body": "armor", "shield": "shield",
                "weapon": "weapon", "backpack": "backpack"}


# Spelling variants a player or the LLM might reach for.
SPELLING_FIXES = {"armour": "armor"}


def _singularize(word):
    """Cheap English singular: torches -> torch, rations -> ration, axes -> axe."""
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "ses", "xes", "zes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us")) and len(word) > 3:
        return word[:-1]
    return word


def normalize_item_name(text, keep_parens=False):
    """Reduce any item name (or a player's guess at one) to a match key.

    'Torches (6)'          -> 'torch'
    'Rope, 50 ft'          -> 'rope 50 ft'
    'Small Wooden Shields' -> 'small wooden shield'
    'Sword, Two-Handed'    -> 'sword two handed'

    Parentheticals are dropped by default because they are usually a quantity
    ('Torches (6)'), but the words inside one can be the only thing telling two
    items apart ('Rope, 50 ft' vs 'Rope, 50 ft (silk)'), so resolve_item_name
    matches against both the stripped and the keep_parens form.
    """
    t = (text or "").lower()
    if not keep_parens:
        t = re.sub(r"\(.*?\)", " ", t)     # quantity/spec parentheticals
    t = re.sub(r"[^a-z0-9 ]+", " ", t)     # commas, hyphens, parens, periods
    words = [_singularize(w) for w in t.split() if w not in _ITEM_STOPWORDS]
    return " ".join(SPELLING_FIXES.get(w, w) for w in words)


def resolve_item_name(wanted, candidates):
    """Match a player-typed item name against a list of exact names.

    Returns (match, ambiguous): `match` is one of `candidates` verbatim (so it
    is safe as a dict key) or None; `ambiguous` holds the equally-plausible
    candidates when the input could not single one out. Matching runs from
    strictest to loosest - case-insensitive exact, normalized exact, unique
    word-phrase match, then unique substring - and stops at the first rung that
    lands on exactly one candidate. Ambiguity is reported rather than guessed
    at: "shield" with both a Small Wooden Shield and a Kite Shield carried
    means ask, not pick.
    """
    cands = [c for c in candidates if c]
    if not cands or not (wanted or "").strip():
        return None, []
    typed = wanted.strip().lower()
    for c in cands:
        if c.lower() == typed:
            return c, []
    key = normalize_item_name(wanted)
    key_full = normalize_item_name(wanted, keep_parens=True)
    if not key:
        return None, []
    words = key.split()
    variants = {c: {normalize_item_name(c), normalize_item_name(c, keep_parens=True)}
                for c in cands}

    def hits(pred):
        return [c for c, vs in variants.items() if any(pred(v) for v in vs)]

    for pred in (
        lambda n: n == key or n == key_full,                    # torch -> Torches (6)
        lambda n: n.startswith(key + " ") or key.startswith(n + " "),   # rope -> Rope, 50 ft
        lambda n: all(w in n.split() for w in words),           # holy water -> Holy Water, Small Vial
        lambda n: key in n or n in key,                         # last-ditch substring
    ):
        found = hits(pred)
        if len(found) == 1:
            return found[0], []
        if len(found) > 1:
            return None, sorted(found)
    return None, []


def find_table_key(name, table):
    """Resolve a name to a key of an item category table (loose matching)."""
    if not name:
        return None
    if name in table:
        return name
    key = normalize_item_name(name)
    for candidate in table:
        if normalize_item_name(candidate) == key:
            return candidate
    return None



# --------------------------------------------------------------------------- #
# Dungeon generator
# --------------------------------------------------------------------------- #
# Chance that the entrance room also happens to keep the shaft going
# down to the next floor (the hidden stair always exists elsewhere).
ENTRANCE_DOWN_CHANCE = 0.33


class DungeonGenerator:
    def __init__(self, seed):
        self.seed = seed
        self.bestiary = {}
        self.encounters = {}
        self._load_data()

    def _load_data(self):
        self.bestiary = _load_json("bestiary.json", {})
        self.encounters = _load_json("encounter_tables.json", {})

    def generate_floor(self, z):
        random.seed(f"{self.seed}_{z}")
        cell_size = 4
        grid_size = 14
        rooms = []
        floor_data = {}

        occupied_cells = [(0, 0)]
        occupied_set = {(0, 0)}
        target_rooms = random.randint(30, 48)

        while len(occupied_cells) < min(target_rooms, grid_size * grid_size):
            cx, cy = random.choice(occupied_cells)
            dx, dy = random.choice([(1, 0), (-1, 0), (0, 1), (0, -1)])
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < grid_size and 0 <= ny < grid_size and (nx, ny) not in occupied_set:
                occupied_cells.append((nx, ny))
                occupied_set.add((nx, ny))

        # Entrance fix: re-root the floor around a RANDOM occupied cell so the
        # dungeon sprawls in every direction from the stairs instead of always
        # dangling to the northeast of a corner entrance at (0, 0).
        anchor_cx, anchor_cy = random.choice(occupied_cells)
        occupied_cells = [(cx - anchor_cx, cy - anchor_cy) for cx, cy in occupied_cells]
        occupied_set = set(occupied_cells)

        rooms = [
            {"x": cx * cell_size, "y": cy * cell_size, "w": cell_size, "h": cell_size}
            for cx, cy in occupied_cells
        ]

        theme_name = None
        if FLOOR_THEMES:
            theme_name = random.choice(list(FLOOR_THEMES.keys()))
        theme = FLOOR_THEMES.get(theme_name, {})

        processed_rooms = {}
        for i, room in enumerate(rooms):
            room_id = f"{room['x']}_{room['y']}_{z}"
            if room["x"] == 0 and room["y"] == 0:
                room_type = "Corridor"
                name = f"Floor {abs(z)} - Entrance"
            else:
                room_type = random.choices(
                    ["Combat", "Loot", "Empty", "Trap", "Shrine", "Puzzle",
                     "Corridor", "Discovery"],
                    weights=[28, 10, 22, 14, 4, 9, 4, 5],
                )[0]
                name = f"Floor {abs(z)} - Chamber {i + 1}"

            container_type = random.choice(
                ["Chest", "Wooden Crate", "Iron Lockbox", "Ancient Urn", "Silk Pouch"]
            )
            lit_chance = max(0, 1.0 - (abs(z) - 1) * 0.2)
            is_lit = random.random() < lit_chance

            if theme:
                visual_tags = [random.choice(theme["walls"]), random.choice(theme["floors"])]
                visual_tags.append(random.choice(theme["light"]) if is_lit
                                   else "oppressive darkness")
                if random.random() < 0.55 and theme.get("details"):
                    visual_tags.append(random.choice(theme["details"]))
            else:
                visual_tags = (["stone walls", "damp air", "torchlight"] if is_lit
                               else ["stone walls", "damp air", "oppressive darkness"])

            processed_rooms[room_id] = {
                "name": name,
                "type": room_type,
                "encounter": self._stock_monster(z) if room_type == "Combat" else "None",
                "loot": self._stock_loot(z) if room_type == "Loot" else None,
                "locked_room": random.random() < 0.1,
                "secret": False,
                "trap": (room_type == "Trap"),
                "container": container_type if room_type == "Loot" else None,
                "chest": None,
                "visual_tags": visual_tags,
                "exits": {},
                "doors": {},
                "obstacles": {},
                "theme": theme_name,
                "atmosphere": theme.get("atmosphere"),
            }

            if room_type == "Discovery":
                processed_rooms[room_id]["discovery"] = random.choice(
                    list(DISCOVERY_DATA.keys()) or ["Abandoned Campsite"])
                processed_rooms[room_id]["discovery_state"] = {}

            if room_type == "Combat" and random.random() < 0.3:
                processed_rooms[room_id]["loot"] = self._stock_loot(z)
                processed_rooms[room_id]["container"] = random.choice(
                    ["Chest", "Sack", "Iron Lockbox"]
                )

            if room_type == "Shrine" and random.random() < 0.5:
                # Half of dungeon shrines are negative: a blackened altar that
                # curses the light and empowers the dark.
                processed_rooms[room_id]["shrine_negative"] = True
                processed_rooms[room_id]["visual_tags"].extend(
                    ["a blackened altar", "candlelight guttering in a cold draft"])

        entrance_id = "0_0_" + str(z)
        if z == -1:
            processed_rooms[entrance_id]["exits"]["Up"] = "0_-1_0"
        elif f"0_0_{z + 1}" in processed_rooms:
            processed_rooms[entrance_id]["exits"]["Up"] = f"0_0_{z + 1}"
        processed_rooms[entrance_id]["stairs_up"] = True

        room_ids = list(processed_rooms.keys())
        if not room_ids:
            return {}

        processed_rooms[entrance_id]["visual_tags"].append("stone stairs leading up")

        # The way down must be explored for: a stair is always hidden in some
        # room on the floor. The shaft under the entrance *sometimes* continues
        # down as well, so descending where you arrived is possible but not a
        # guaranteed outcome. (z == -8 is the bottom of the dungeon.)
        if z != -8:
            stairs_down_id = (random.choice([r for r in room_ids if r != entrance_id])
                              if len(room_ids) > 1 else entrance_id)
            processed_rooms[stairs_down_id]["stairs_down"] = True
            processed_rooms[stairs_down_id]["visual_tags"].append("stone stairs leading down")
            if stairs_down_id != entrance_id and random.random() < ENTRANCE_DOWN_CHANCE:
                processed_rooms[entrance_id]["stairs_down"] = True
                processed_rooms[entrance_id]["visual_tags"].append("stone stairs leading down")

        for feature_type, count_roll in [("secret", random.randint(1, 4)),
                                         ("trap", random.randint(1, 4)),
                                         ("chest", random.randint(1, 4))]:
            selected_rooms = random.sample(room_ids, min(count_roll, len(room_ids)))
            for r_id in selected_rooms:
                if feature_type == "secret":
                    processed_rooms[r_id]["secret"] = True
                    processed_rooms[r_id]["loot"] = self._stock_loot(z, high_value=True)
                elif feature_type == "trap":
                    processed_rooms[r_id]["trap"] = True
                elif feature_type == "chest":
                    processed_rooms[r_id]["container"] = random.choice(
                        ["Chest", "Wooden Crate", "Iron Lockbox", "Ancient Urn"]
                    )
                    processed_rooms[r_id]["chest"] = {"item": self._stock_loot(z),
                                                      "locked": random.random() < 0.5}

        for r_id, r_data in processed_rooms.items():
            if r_data["trap"]:
                depth = abs(int(r_id.split("_")[-1]))
                lethality = "low" if depth < 3 else "medium" if depth < 6 else "high"
                eligible = [k for k, v in TRAP_DATA.items() if v["lethality"] == lethality]
                r_data["trap_type"] = random.choice(eligible)

        cell_by_grid = {}
        for room_id in processed_rooms:
            x, y, _ = map(int, room_id.split("_"))
            cell_by_grid[(x // cell_size, y // cell_size)] = room_id

        adjacency = []
        for (cx, cy), room_id in cell_by_grid.items():
            for dx, dy, direction in [(1, 0, "East"), (-1, 0, "West"),
                                      (0, 1, "North"), (0, -1, "South")]:
                neighbor = (cx + dx, cy + dy)
                if neighbor in cell_by_grid:
                    neighbor_id = cell_by_grid[neighbor]
                    if room_id < neighbor_id:
                        adjacency.append((room_id, neighbor_id, direction))

        # Classify every adjacency edge BEFORE linking: open way, closed
        # door, locked door, or obstacle. Puzzle rooms always obstruct at
        # least one of their ways, so a puzzle is a barrier, not scenery.
        edge_kind = {}
        for edge in adjacency:
            roll = random.random()
            if roll < LOCKED_DOOR_CHANCE:
                edge_kind[edge] = "locked_door"
            elif roll < LOCKED_DOOR_CHANCE + CLOSED_DOOR_CHANCE:
                edge_kind[edge] = "closed_door"
            elif roll < LOCKED_DOOR_CHANCE + CLOSED_DOOR_CHANCE + OBSTACLE_EDGE_CHANCE:
                edge_kind[edge] = "obstacle"
        obstructed_rooms = set()
        for edge, kind in edge_kind.items():
            obstructed_rooms.update((edge[0], edge[1]))
        for r_id, r_data in processed_rooms.items():
            if r_data["type"] != "Puzzle" or r_id in obstructed_rooms:
                continue
            candidates = [e for e in adjacency if r_id in (e[0], e[1])]
            if candidates:
                edge_kind[random.choice(candidates)] = "obstacle"

        OPPOSITE = {"East": "West", "West": "East", "North": "South", "South": "North"}

        def _link(a_id, b_id, direction):
            back = OPPOSITE[direction]
            for rid, d in ((a_id, direction), (b_id, back)):
                r = processed_rooms[rid]
                if d in r["exits"] or d in r["doors"] or d in r["obstacles"]:
                    return False
            kind = edge_kind.get((a_id, b_id, direction), "open")
            if kind == "closed_door":
                processed_rooms[a_id]["doors"][direction] = {"to": b_id, "state": "closed"}
                processed_rooms[b_id]["doors"][back] = {"to": a_id, "state": "closed"}
            elif kind == "locked_door":
                processed_rooms[a_id]["doors"][direction] = {"to": b_id, "state": "locked"}
                processed_rooms[b_id]["doors"][back] = {"to": a_id, "state": "locked"}
            elif kind == "obstacle":
                oname = random.choice(list(OBSTACLE_DATA.keys()))
                processed_rooms[a_id]["obstacles"][direction] = {"to": b_id, "name": oname}
                processed_rooms[b_id]["obstacles"][back] = {"to": a_id, "name": oname}
            else:
                processed_rooms[a_id]["exits"][direction] = b_id
                processed_rooms[b_id]["exits"][back] = a_id
            return True


        parent = {room_id: room_id for room_id in processed_rooms}

        def _find(node):
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def _union(a, b):
            root_a, root_b = _find(a), _find(b)
            if root_a == root_b:
                return False
            parent[root_b] = root_a
            return True

        maze_edges = adjacency[:]
        random.shuffle(maze_edges)
        spanning_tree_edges = []
        for a_id, b_id, direction in maze_edges:
            if _union(a_id, b_id):
                if _link(a_id, b_id, direction):
                    spanning_tree_edges.append((a_id, b_id, direction))

        loop_candidates = [e for e in adjacency if e not in spanning_tree_edges]
        random.shuffle(loop_candidates)
        extra_loop_target = max(5, len(processed_rooms) // 3)
        added_loops = 0
        for a_id, b_id, direction in loop_candidates:
            if added_loops >= extra_loop_target:
                break
            if _link(a_id, b_id, direction):
                added_loops += 1

        for room_id in processed_rooms:
            non_vertical_exits = [d for d in processed_rooms[room_id]["exits"] if d in OPPOSITE]
            if len(non_vertical_exits) >= 2:
                continue
            for a_id, b_id, direction in adjacency:
                if room_id not in (a_id, b_id):
                    continue
                if _link(a_id, b_id, direction):
                    break

        for r_id, r_data in processed_rooms.items():
            if not r_data.get("trap"):
                continue
            if TRAP_DATA.get(r_data.get("trap_type"), {}).get("location") != "Exit":
                continue
            options = sorted(d for d in list(r_data["exits"]) + list(r_data["doors"])
                             + list(r_data["obstacles"]) if d in OPPOSITE)
            if options:
                r_data["trap_exit"] = random.choice(options)

        return processed_rooms

    def _intersects(self, r1, r2):
        return (r1["x"] < r2["x"] + r2["w"] and r1["x"] + r1["w"] > r2["x"] and
                r1["y"] < r2["y"] + r2["h"] and r1["y"] + r1["h"] > r2["y"])

    def _stock_monster(self, z):
        depth = abs(z)
        floor_key = str(depth)
        eligible_monsters = self.encounters.get(floor_key, self.encounters.get("1", ["Goblin"]))
        return random.choice(eligible_monsters)

    def _stock_loot(self, z, high_value=False):
        depth = abs(z)
        value_caps = {1: 25, 2: 50, 3: 100, 4: 200, 5: 400, 6: 800, 7: 1600, 8: 3000}
        cap = value_caps.get(depth, 3000)
        if high_value:
            cap *= 2
        valid_categories = [REAGENT_DATA, WEAPON_DATA, ARMOR_DATA, GENERAL_DATA, KEY_DATA, POUCH_DATA]
        eligible_items = []
        for cat in valid_categories:
            for item, info in cat.items():
                price = info["price"] if isinstance(info, dict) else info
                if price <= cap:
                    eligible_items.append(item)

        if random.random() < 0.65:
            item_to_give = None
            item_price = 0
            if random.random() < 0.05:
                # A spell readable: mostly a page, sometimes a scrap, rarely a book.
                spell_name = random.choice(list(SPELL_DATA.keys()))
                roll = random.random()
                if roll < 0.10:
                    item_to_give = (f"Book: {spell_name}"
                                    if f"Book: {spell_name}" in READABLE_DATA
                                    else f"Scrap: {spell_name}")
                elif roll < 0.40:
                    item_to_give = f"Scrap: {spell_name}"
                else:
                    item_to_give = f"Page: {spell_name}"
                item_price = LEDGER_DATA.get(item_to_give, 0)
            elif eligible_items:
                item_to_give = random.choice(eligible_items)
                item_price = LEDGER_DATA.get(item_to_give, 0)
            else:
                item_to_give = "Old Rags"
                item_price = 0
            gold_to_give = random.randint(0, max(0, cap - item_price))
            return {"item": item_to_give, "gold": gold_to_give}
        else:
            return {"item": None, "gold": random.randint(0, cap)}


# --------------------------------------------------------------------------- #
# Player state
# --------------------------------------------------------------------------- #
def default_player_data():
    """The pristine starting character (used on first run and on 'reset')."""
    return {
        "max_hp": 10, "hp": 10, "satiety": 100, "max_satiety": 100,
        "item_ages": {},
        "virtue": 0, "gold": 100, "status_effects": [],
        "virtue_ledger": {"day": 0, "last_stand": 0, "floors": []},
        "light_fuel": {"torch": 0, "lantern": 0},
        "light_duration": 0,
        "buffs": [],
        "time": {"day": 1, "phase": 0, "tick": 0},
        "stats": {"STR": 11, "DEX": 11, "CON": 11, "INT": 11, "WIS": 11, "CHA": 11},
        "position": {"x": -1, "y": 1, "z": 0},
        "last_direction": None,
        "inventory": {"Cloth Robe": 1},
        "equipped": {"weapon": None, "armor": "Cloth Robe", "shield": None},
        "quest": {"active": None, "pending": None, "recent": [], "declined": []},
    }


class PlayerState:
    def __init__(self, game_engine=None, file=None):
        self.engine = game_engine
        # `file` lets a satellite load its own continuously-saved slot; when
        # omitted the classic global PLAYER_FILE is used (the single run, or
        # the Caller in multi mode, which set_mode already re-pointed).
        self.file = file if file is not None else PLAYER_FILE
        self.data = default_player_data()
        self.load()
        self.refresh_derived_stats()

    def load(self):
        if os.path.exists(self.file):
            try:
                with open(self.file, "r") as f:
                    self.data.update(json.load(f))
            except json.JSONDecodeError:
                pass
        self.data.setdefault("time", {"day": 1, "phase": 0, "tick": 0})
        self.data.setdefault("item_ages", {})

    def save(self):
        d = os.path.dirname(self.file)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(self.file, "w") as f:
            json.dump(self.data, f, indent=4)

    def refresh_derived_stats(self):
        con_bonus = self.data["stats"]["CON"] - 10
        self.data["max_hp"] = 10 + (con_bonus * 2)
        self.data["hp"] = self.data["max_hp"]

    def die(self):
        if self.engine:
            self.engine.death_message = DEATH_MESSAGE
            self.engine.world.wipe_floors(self.data["position"]["z"])
            self.data["inventory"] = {"Cloth Robe": 1}
            self.data["equipped"] = {"weapon": None, "armor": "Cloth Robe", "shield": None}
            self.data["position"] = {"x": -1, "y": 1, "z": 0}
            self.data["status_effects"] = []
            self.data["poison_duration"] = 0
            self.data["satiety"] = self.data["max_satiety"]
            led = self.data.get("virtue_ledger")
            if isinstance(led, dict):
                led["floors"] = []
                led["last_stand"] = 0
            if self.data["gold"] < 100:
                self.data["gold"] = 100
            self.refresh_derived_stats()
            self.save()
        return DEATH_MESSAGE

    def is_down(self):
        """Multiplayer satellites only: 'down' for the session (C5)."""
        return bool(self.data.get("down"))

    def update_hp(self, amount):
        self.data["hp"] = max(0, min(self.data["max_hp"], self.data["hp"] + int(amount)))
        self.save()
        if self.data["hp"] <= 0:
            # A satellite that reaches 0 HP is 'down' for the session (C5) -
            # the engine handles revival; no floor wipe / respawn / refund.
            if self.data.get("is_satellite") and self.engine:
                self.engine._sat_on_down(self)
                return 0
            return self.die()
        return self.data["hp"]

    def update_satiety(self, amount):
        self.data["satiety"] = max(0, min(self.data["max_satiety"], self.data["satiety"] + amount))
        # Time passes when the body spends itself. Eating refills the tank;
        # it never turns the clock back.
        if amount < 0 and self.engine:
            self.engine.advance_time(-int(amount))
        self.save()

    def update_virtue(self, amount):
        self.data["virtue"] += int(amount)
        self.save()

    def is_lit(self):
        eq = self.data["equipped"]
        fuel = self.data["light_fuel"]
        if eq.get("weapon") == "Torch" or "Torch" in eq.values():
            if fuel["torch"] > 0:
                return True
        if "Lantern" in eq.values() and fuel["lantern"] > 0:
            return True
        if self.data["light_duration"] > 0:
            return True
        return False

    def update_gold(self, amount):
        self.data["gold"] = max(0, min(self.data["gold"] + int(amount), 1000000))
        self.save()

    def move(self, dx, dy, dz):
        self.data["position"]["x"] += dx
        self.data["position"]["y"] += dy
        self.data["position"]["z"] += dz
        self.update_satiety(-1)
        self.save()

    def get_inventory_capacity(self, inventory=None):
        if inventory is None:
            inventory = self.data["inventory"]
        capacity = 5 + (self.data["stats"]["STR"] - 10)
        if inventory.get("Backpack", 0) > 0:
            capacity += 12
        # Sack slots: max 2, shared between Sacks and Reagent Pouches.
        # A Reagent Pouch claims a sack slot (its bonus is reagent storage,
        # not +4 slots), forcing the loot-vs-reagents choice.
        pouch_slots = min(inventory.get("Reagent Pouch", 0), 2)
        sack_slots = min(inventory.get("Sack", 0), 2 - pouch_slots)
        capacity += sack_slots * 4
        return capacity

    def get_inventory_usage(self, inventory):
        usage = 0
        has_quiver = inventory.get("Quiver", 0) > 0
        has_pouch = inventory.get("Reagent Pouch", 0) > 0
        stackables = ["Gold", "Coin", "Gem"]
        for item, qty in inventory.items():
            if qty <= 0:
                continue
            if item == "Arrows":
                usage += 1 if has_quiver else qty
            elif item in REAGENT_DATA and has_pouch:
                continue  # stored in the Reagent Pouch
            elif item in stackables or item in REAGENT_DATA or item in PROVISIONS_DATA:
                usage += 1
            elif item in WEAPON_DATA or item in ARMOR_DATA:
                usage += qty
            else:
                usage += qty
        return usage

    def remove_item(self, item, quantity=1):
        quantity = int(quantity)
        match, _ = resolve_item_name(item, list(self.data["inventory"]))
        if match:
            item = match
        current_qty = self.data["inventory"].get(item, 0)
        if current_qty < quantity:
            return {"success": False, "message": f"You don't have enough {item} to remove."}
        self.data["inventory"][item] -= quantity
        if self.data["inventory"][item] <= 0:
            del self.data["inventory"][item]
            self.data.get("item_ages", {}).pop(item, None)
        self.save()
        return {"success": True, "message": f"Removed {quantity}x {item} from inventory."}

    def equip_item(self, item):
        """Equip something carried, matched loosely by name.

        Players type "equip torch", "equip shield", "equip two handed sword" -
        the inventory key may be a bundle ("Torches (6)"), a ledger name with a
        comma in it ("Axe, Battle"), or plural. Returns the slot it landed in so
        the caller can be specific about what happened.
        """
        inv = self.data["inventory"]
        held = [k for k, q in inv.items() if q > 0]
        match, ambiguous = resolve_item_name(item, held)
        if ambiguous:
            return {"success": False,
                    "message": "Which one - " + " or ".join(ambiguous) + "?"}
        if not match:
            return {"success": False,
                    "message": f"You don't have {item} in your inventory."}
        inv_key = match
        key = normalize_item_name(inv_key)
        slot, canonical = None, inv_key
        if key == "backpack":
            slot, canonical = "backpack", "Backpack"
        elif key in LIGHT_FUEL_KEYS:
            # Lights are not in the weapons table, and the burn-down maths
            # compares against the unit names "Torch"/"Lantern" - store those.
            # They occupy the SHIELD hand: a torch in the weapon hand would
            # leave the player unable to fight (and largely defenseless).
            slot, canonical = "shield", LIGHT_FUEL_KEYS[key]
        else:
            wkey = find_table_key(inv_key, WEAPON_DATA) or find_table_key(inv_key, RANGED_DATA)
            if wkey:
                slot, canonical = "weapon", wkey
            else:
                akey = find_table_key(inv_key, ARMOR_DATA)
                if akey:
                    armor_type = ARMOR_DATA[akey].get("type")
                    if armor_type == "body":
                        slot, canonical = "armor", akey
                    elif armor_type == "shield":
                        slot, canonical = "shield", akey
        if slot is None:
            return {"success": False, "message": f"{inv_key} cannot be equipped."}
        self.data["equipped"][slot] = canonical
        # A bundle added outside the shop path (loot, narrative, char gen) can
        # arrive with no fuel attached - otherwise the torch equips and stays dark.
        if canonical in ("Torch", "Lantern"):
            fuel_key = "torch" if canonical == "Torch" else "lantern"
            fuel = self.data.setdefault("light_fuel", {"torch": 0, "lantern": 0})
            if fuel.get(fuel_key, 0) <= 0:
                units = inv.get(inv_key, 0)
                units *= BUNDLES[inv_key][1] if inv_key in BUNDLES else 1
                fuel[fuel_key] = max(0, units) * (60 if canonical == "Torch" else 120)
        self.save()
        return {"success": True, "message": f"Equipped {canonical}.", "slot": slot}

    def unequip_item(self, item):
        """Unequip by slot name ('shield', 'weapon') or by item name.

        Slot names are matched case-insensitively; item names go through the
        same loose resolver as equip, so "unequip small wooden shield" works
        even though the ledger spells it "Small Wooden Shield".
        """
        eq = self.data["equipped"]
        wanted = (item or "").strip()
        if not wanted:
            return {"success": False, "message": "Unequip what?"}
        word = wanted.lower()
        slots = {s.lower(): s for s in eq}
        slot = slots.get(word) or slots.get(SLOT_ALIASES.get(word, ""))
        if slot:
            if eq[slot]:
                removed = eq[slot]
                eq[slot] = None
                self.save()
                return {"success": True,
                        "message": f"Unequipped {removed} from the {slot} slot."}
            return {"success": False, "message": f"Your {slot} slot is already empty."}
        match, ambiguous = resolve_item_name(wanted, [v for v in eq.values() if v])
        if ambiguous:
            return {"success": False,
                    "message": "Which one - " + " or ".join(ambiguous) + "?"}
        if match:
            for s, worn in eq.items():
                if worn == match:
                    slot = s
                    break
            eq[slot] = None
            self.save()
            return {"success": True,
                    "message": f"Unequipped {match} from the {slot} slot."}
        return {"success": False,
                "message": f"You are not currently wearing or wielding {item}."}

    def discard_item(self, item, quantity=None):
        """Throw an item (or some of a stack) away from the pack for good.

        Headless/pure: resolves the name the same loose way as remove/equip,
        clamps the quantity to what is actually carried, removes it, and clears
        any equipped slot wearing the very item the stack just emptied. Returns a
        dict so the CLI/LLM can be specific about what happened. `quantity=None`
        means the whole stack.
        """
        inv = self.data["inventory"]
        match, ambiguous = resolve_item_name(item, [k for k, q in inv.items() if q > 0])
        if ambiguous:
            return {"success": False, "message": "Which one - " + " or ".join(ambiguous) + "?"}
        if not match:
            return {"success": False, "message": f"You don't have {item} to discard."}
        have = inv.get(match, 0)
        if quantity is None:
            qty = have
        else:
            qty = int(quantity)
            if qty <= 0:
                qty = have
        qty = max(1, min(qty, have))
        res = self.remove_item(match, qty)
        if not res.get("success"):
            return res
        still = inv.get(match, 0)
        cleared = []
        if still <= 0:
            eq = self.data["equipped"]
            norm_match = normalize_item_name(match)
            for slot, worn in eq.items():
                if not worn:
                    continue
                if worn == match or normalize_item_name(worn) == norm_match:
                    eq[slot] = None
                    cleared.append(f"{slot} ({worn})")
            if cleared:
                self.save()
        if still > 0:
            msg = f"You throw away {qty}x {match} (keeping {still})."
        else:
            msg = f"You throw away {qty}x {match}."
        if cleared:
            msg += f" Your {', '.join(cleared)} is now empty."
        if self.engine:
            pos = self.data["position"]
            self.engine.world.add_dropped(pos["x"], pos["y"], pos["z"], match, qty)
        return {
            "success": True,
            "discarded": qty,
            "item": match,
            "have": still,
            "cleared": cleared,
            "message": msg,
        }


    def add_item(self, item, quantity=1):
        bundles = BUNDLES
        if item in bundles:
            unit_name, unit_size = bundles[item]
            item = unit_name
            quantity = int(quantity) * unit_size
        else:
            quantity = int(quantity)
        temp_inv = self.data["inventory"].copy()
        temp_inv[item] = temp_inv.get(item, 0) + quantity
        if (item in REAGENT_DATA and temp_inv.get("Reagent Pouch", 0) > 0
                and temp_inv[item] > 20):
            return {"success": False,
                    "message": "Your Reagent Pouch can hold up to 20 of each reagent."}
        if self.get_inventory_usage(temp_inv) > self.get_inventory_capacity(temp_inv):
            return {"success": False, "message": "Your inventory is full!"}
        self.data["inventory"][item] = self.data["inventory"].get(item, 0) + quantity
        if item in PERISHABLE_FOODS:
            self.data.setdefault("item_ages", {}).setdefault(item, 0)
        if item == "Torch":
            self.data["light_fuel"]["torch"] += quantity * 60
        elif item == "Oil (Lantern, 1 pint)":
            self.data["light_fuel"]["lantern"] += quantity * 120
        self.save()
        return {"success": True, "message": f"Added {quantity}x {item} to inventory."}

    def drop_pickup(self, x, y, z, item, qty=1):
        """Pick a dropped item off the floor of a room back into the pack."""
        w = self.engine.world
        room_id = f"{x}_{y}_{z}"
        src = w.town_rooms if room_id in w.town_rooms else w.rooms
        drops = (src.get(room_id) or {}).get("dropped_items") or []
        for i, d in enumerate(drops):
            if d.get("name") == item:
                inv = self.data["inventory"]
                if (self.get_inventory_usage(inv) + qty
                        > self.get_inventory_capacity(inv)):
                    return {"success": False,
                            "message": "Your inventory is full - you cannot carry that."}
                drops.pop(i)
                inv[item] = inv.get(item, 0) + qty
                self.data.setdefault("item_ages", {}).setdefault(item, 0)
                if item == "Torch":
                    self.data["light_fuel"]["torch"] += qty * 60
                elif item == "Oil (Lantern, 1 pint)":
                    self.data["light_fuel"]["lantern"] += qty * 120
                w.save()
                self.save()
                left = f" ({len(drops)} item(s) still on the ground here)" if drops else ""
                return {"success": True,
                        "message": f"You pick up {qty}x {item}." + left}
        return {"success": False, "message": "There is nothing like that lying here."}


def time_of_day_description(room, phase_idx, z):
    """Pick the time-of-day description variant for a room.

    Convention (data/town_map.json): a room may carry a "time_descriptions"
    object with keys morning/day/evening/night. Outdoor rooms carry all four;
    indoor rooms share 'morning' (used for Morning/Day) and 'evening' (used
    for Evening/Night). Only applies above ground (z == 0). Returns None to
    fall back to the room's base description.
    """
    if z != 0:
        return None
    tdesc = room.get("time_descriptions")
    if not tdesc:
        return None
    idx = int(phase_idx) % 4
    # 4 keys -> full cycle; 3 keys -> night borrows evening;
    # 2 keys -> Morning/Day share 'morning', Evening/Night share 'evening'.
    table = {4: ["morning", "day", "evening", "night"],
             3: ["morning", "day", "evening", "evening"],
             2: ["morning", "morning", "evening", "evening"]}
    keys = table.get(len(tdesc), ["morning", "morning", "evening", "evening"])
    return tdesc.get(keys[idx]) or tdesc.get("morning") or tdesc.get("evening")



def room_features(room):
    """Factual list of the unanimate features ACTUALLY present in a room right
    now, so narration never calls a treasure room 'empty'. This is deliberately
    limited to objects (chest/loot, trap, shrine) - living actors (NPC, monster,
    corpse) are reported separately in npc_presence, so the two lines complement
    each other instead of duplicating. Returns a short sentence fragment."""
    feats = []
    loot_present = ((room.get("loot") or room.get("chest"))
                    and not room.get("loot_taken"))
    if loot_present:
        cont = room.get("container") or (room.get("chest") or {}).get("item") or "chest"
        locked = bool((room.get("chest") or {}).get("locked")) or room.get("locked_room")
        feats.append(f"a {cont}" + (" (locked)" if locked else ""))
    if room.get("trap") and not room.get("disarmed"):
        feats.append(f"a trap in the floor ({room.get('trap_type', 'a hidden trigger')})")
    if room.get("shrine") or room.get("type") == "Shrine":
        feats.append("a shrine/altar")
    return "; ".join(feats) if feats else "nothing of note"

# --------------------------------------------------------------------------- #
# World map
# --------------------------------------------------------------------------- #
class WorldMap:
    def __init__(self, seed=None):
        self.seed = seed if seed is not None else random.randint(0, 1000000)
        self.engine_phase = 0
        self.rooms = {}
        self.town_rooms = {}
        self.ledger = {}
        self.generator = DungeonGenerator(self.seed)
        self.load()

    def load(self):
        if os.path.exists(WORLD_FILE):
            try:
                with open(WORLD_FILE, "r") as f:
                    self.rooms = json.load(f)
            except json.JSONDecodeError:
                pass
            self._migrate_ways()
        self.town_rooms = _load_json("town_map.json", {})
        load_ledger()
        self.ledger = LEDGER

    def _migrate_ways(self):
        """Old saves carry a room-level 'obstacle' string and no doors/obstacles
        dicts. Attach any such obstacle to one horizontal way (that side it
        stands on) and let everything else stand as-is."""
        changed = False
        for rid, room in self.rooms.items():
            if not isinstance(room, dict):
                continue
            if "doors" not in room:
                room["doors"] = {}
                changed = True
            if "obstacles" not in room:
                room["obstacles"] = {}
                changed = True
            ob = room.pop("obstacle", None) if room.get("type") != "Puzzle" else room.get("obstacle")
            if room.get("type") == "Puzzle" and ob:
                horiz = [d for d in list(room.get("exits", {}))
                         if d in ("North", "South", "East", "West")]
                if horiz:
                    d = random.choice(horiz)
                    far_id = room["exits"].pop(d)
                    room["obstacles"][d] = {"to": far_id, "name": ob}
                    far = self.rooms.get(far_id)
                    back = {"North": "South", "South": "North", "East": "West",
                            "West": "East"}[d]
                    if far is not None and far.get("exits", {}).get(back) == rid:
                        far["exits"].pop(back)
                        far.setdefault("obstacles", {})[back] = {"to": rid, "name": ob}
                room.pop("obstacle", None)
                room["type"] = "Empty"
                changed = True
        if changed:
            self.save()

    def add_dropped(self, x, y, z, name, qty=1, decay=None):
        """Record dropped items on the TRUE room dict (get_room() hands out
        copies, so mutating those would vanish)."""
        room_id = f"{x}_{y}_{z}"
        src = self.town_rooms if room_id in self.town_rooms else self.rooms
        if room_id not in src:
            return False
        drops = src[room_id].setdefault("dropped_items", [])
        for _ in range(qty):
            drops.append({"name": name,
                          "decay": decay if decay is not None
                          else random.randint(*DROP_FADE_MOVES)})
        self.save()
        return True

    def decay_drops(self, x, y, z):
        """Advance the hidden per-item clock (in MOVES) for the room entered.
        Returns a list of (name, qty) that just faded away."""
        room_id = f"{x}_{y}_{z}"
        src = self.town_rooms if room_id in self.town_rooms else self.rooms
        drops = (src.get(room_id) or {}).get("dropped_items") or []
        if not drops:
            return []
        gone = {}
        keep = []
        for d in drops:
            d["decay"] = d.get("decay", 5) - 1
            if d["decay"] <= 0:
                gone[d["name"]] = gone.get(d["name"], 0) + 1
            else:
                keep.append(d)
        src[room_id]["dropped_items"] = keep
        if len(keep) != len(drops):
            self.save()
        return list(gone.items())


    def save(self):
        with open(WORLD_FILE, "w") as f:
            json.dump(self.rooms, f, indent=4)

    def wipe_floors(self, z_threshold):
        keys_to_delete = [rid for rid in self.rooms if int(rid.split("_")[-1]) <= z_threshold]
        for k in keys_to_delete:
            del self.rooms[k]
        self.save()

    def get_room(self, x, y, z):
        room_id = f"{x}_{y}_{z}"
        room = None
        if room_id in self.town_rooms:
            room = self.town_rooms[room_id].copy()
        elif room_id in self.rooms:
            room = self.rooms[room_id].copy()
        elif z < 0:
            if not any(int(rid.split("_")[-1]) == z for rid in self.rooms):
                floor_rooms = self.generator.generate_floor(z)
                self.rooms.update(floor_rooms)
                self.save()
            room = self.rooms.get(room_id, {
                "name": "Dark Corridor", "type": "Corridor", "encounter": "None",
                "visual_tags": ["darkness", "stone"], "exits": {},
            }).copy()
        else:
            room = {"name": "The Void", "type": "Void",
                    "description": "There is nothing here.", "exits": {}}

        tdesc = time_of_day_description(room, self.engine_phase, z)
        if tdesc:
            room["description"] = tdesc
        desc = room.get("description", "No description available.")
        tags = ", ".join(room.get("visual_tags", [])) if room.get("visual_tags") else "none"
        stairs_info = []
        if room.get("stairs_up"):
            stairs_info.append("stairs leading up")
        if room.get("stairs_down"):
            stairs_info.append("stairs leading down")
        stairs_text = f" Also, note that there are {', '.join(stairs_info)}." if stairs_info else ""
        room["narrative_hint"] = (
            f"NARRATIVE HINT: You are describing this room to the player. "
            f"Use the following base description: '{desc}'. Crucially, weave these visual "
            f"elements into your prose so the player can 'see' them: {tags}.{stairs_text}"
        )
        return room


# --------------------------------------------------------------------------- #
# Game engine
# --------------------------------------------------------------------------- #
class GameEngine:
    def __init__(self):
        self.world = WorldMap()
        self.player = PlayerState(self)
        self.world.engine_phase = self.player.data.setdefault(
            "time", {"day": 1, "phase": 0, "tick": 0})["phase"]
        self.turn_count = 0
        self.combat_state = {}
        self.monster_control = {}
        self.death_message = None
        self.pending_food_msgs = []
        self.last_parley_verdict = None
        # ---- multiplayer party: the Caller (self.player) + satellites ----
        # Satellites are co-located at the Caller's position (C1): they carry
        # their own HP/gold/gear but NO position. In single mode the list is
        # empty and every code path below is exactly the classic run.
        self.satellites = []
        self.satellites_enabled = False

    # ---- party / satellites (multiplayer) ----
    @property
    def party(self):
        """[Caller, *satellites] - the full co-located party."""
        return [self.player] + self.satellites

    def active_satellites(self):
        """Satellites not currently 'down' (C5)."""
        return [s for s in self.satellites if not s.is_down()]

    def satellite_by_slot(self, slot):
        for s in self.satellites:
            if s.slot == slot:
                return s
        return None

    def load_satellites(self, party_size):
        """Build the satellite roster for a multi session. P1 is the Caller
        (self.player, already loaded); P2..P(party_size-1) become satellites,
        each from its own continuously-saved slot file."""
        self.satellites = []
        if MODE != "multi":
            self.satellites_enabled = False
            return self.satellites
        n = max(1, min(MP_MAX, int(party_size)))
        self.satellites_enabled = n >= 2
        for i in range(1, n):  # slots P2..Pn
            slot = MP_SLOTS[i]
            sat = PlayerState(self, file=mp_slot_file(slot))
            sat.slot = slot
            sat.data.setdefault("is_satellite", True)
            sat.data.setdefault("down", False)
            sat.save()
            self.satellites.append(sat)
        return self.satellites

    def _sat_on_down(self, sat):
        """A satellite reaches 0 HP: it is 'down' for the session (C5) - no
        floor wipe, no respawn, no gold refund. The Caller can revive it."""
        sat.data["down"] = True
        sat.data["hp"] = 0
        sat.save()

    def revive_satellite(self, slot):
        """Bring a 'down' satellite back to full health (temple revival)."""
        sat = self.satellite_by_slot(slot)
        if not sat or not sat.data.get("down"):
            return None
        sat.data["down"] = False
        sat.refresh_derived_stats()
        sat.data["hp"] = sat.data["max_hp"]
        sat.save()
        return sat

    def transfer(self, slot, item, qty=1, direction="to"):
        """Move an item or 'gold' between the Caller and a satellite.

        direction 'to'   -> Caller hands to the satellite.
        direction 'from' -> the satellite hands back to the Caller.
        Only the Caller holds keys / inn-key (per the design); a satellite can
        carry gear and gold but not unlock things on its own.
        Returns {'success': bool, 'message': str}.
        """
        sat = self.satellite_by_slot(slot)
        if not sat:
            return {"success": False, "message": f"{slot} is not in this party."}
        qty = max(1, int(qty))
        # an equipped item cannot be handed off (it would desync the slot)
        if item != "gold":
            src_p = self.player if direction == "to" else sat
            if item in src_p.data["equipped"].values():
                return {"success": False,
                        "message": f"{item} is equipped - unequip it first."}
        if item == "gold":
            if direction == "to":
                if self.player.data["gold"] < qty:
                    return {"success": False, "message": f"You do not have {qty} gold."}
                self.player.update_gold(-qty)
                sat.update_gold(qty)
                return {"success": True, "message": f"You hand {qty} gold to {slot}."}
            else:
                if sat.data["gold"] < qty:
                    return {"success": False, "message": f"{slot} does not have {qty} gold."}
                sat.update_gold(-qty)
                self.player.update_gold(qty)
                return {"success": True, "message": f"{slot} hands you {qty} gold."}
        # item transfer
        if direction == "to":
            if self.player.data["inventory"].get(item, 0) < qty:
                return {"success": False, "message": f"You do not have {qty} x {item}."}
            rm = self.player.remove_item(item, qty)
            if isinstance(rm, dict) and not rm.get("success"):
                return {"success": False, "message": rm.get("message", f"Cannot hand {item}.")}
            add = sat.add_item(item, qty)
            if isinstance(add, dict) and not add.get("success"):
                # refund and report
                self.player.add_item(item, qty)
                return {"success": False, "message": add.get("message", f"{slot} cannot carry {item}.")}
            return {"success": True, "message": f"You hand {qty} x {item} to {slot}."}
        else:
            if sat.data["inventory"].get(item, 0) < qty:
                return {"success": False, "message": f"{slot} does not have {qty} x {item}."}
            rm = sat.remove_item(item, qty)
            if isinstance(rm, dict) and not rm.get("success"):
                return {"success": False, "message": rm.get("message", f"{slot} cannot hand {item}.")}
            add = self.player.add_item(item, qty)
            if isinstance(add, dict) and not add.get("success"):
                sat.add_item(item, qty)
                return {"success": False, "message": add.get("message", f"You cannot carry {item}.")}
            return {"success": True, "message": f"{slot} hands you {qty} x {item}."}

    def roll_dice(self, sides=20, modifier=0):
        return random.randint(1, int(sides)) + int(modifier)

    # --- time of day ---
    def _phase_name(self, idx=None):
        t = self.player.data.setdefault("time", {"day": 1, "phase": 0, "tick": 0})
        return TIME_PHASES[t["phase"] if idx is None else idx]

    def advance_time(self, ticks=1):
        """Advance the world clock. One tick == one satiety point spent.
        Returns True if the clock rolled over into a new day."""
        t = self.player.data.setdefault("time", {"day": 1, "phase": 0, "tick": 0})
        t["tick"] += int(ticks)
        rolled = False
        while t["tick"] >= PHASE_TICKS:
            t["tick"] -= PHASE_TICKS
            t["phase"] += 1
            if t["phase"] >= len(TIME_PHASES):
                t["phase"] = 0
                t["day"] += 1
                rolled = True
        self.world.engine_phase = t["phase"]
        # The party shares the Caller's clock; age each member's perishables.
        for member in self.party:
            self._age_perishables(int(ticks), member)
        self._inn_tick()
        return rolled

    def advance_phases(self, n):
        """Jump the clock forward by whole phases (sleep). Ticks reset."""
        t = self.player.data.setdefault("time", {"day": 1, "phase": 0, "tick": 0})
        t["phase"] += int(n)
        while t["phase"] >= len(TIME_PHASES):
            t["phase"] -= len(TIME_PHASES)
            t["day"] += 1
        t["tick"] = 0
        self.world.engine_phase = t["phase"]
        self._age_perishables(int(n) * PHASE_TICKS)

    def sleep_until_morning(self):
        """The inn always delivers you to the next Morning."""
        t = self.player.data.setdefault("time", {"day": 1, "phase": 0, "tick": 0})
        t["day"] += 1
        t["phase"] = 0
        t["tick"] = 0
        self.world.engine_phase = 0
        self._age_perishables(len(TIME_PHASES) * PHASE_TICKS)

    def _age_perishables(self, ticks, player=None):
        """Advance food spoilage with the clock; queue flavor for turned items."""
        if ticks <= 0:
            return
        p = player if player is not None else self.player
        inv = p.data["inventory"]
        ages = p.data.setdefault("item_ages", {})
        turned = False
        for name, limit in PERISHABLE_FOODS.items():
            if inv.get(name, 0) <= 0:
                ages.pop(name, None)
                continue
            ages[name] = ages.get(name, 0) + ticks
            if ages[name] >= limit:
                qty = inv.pop(name)
                ages.pop(name, None)
                inv[SPOILED_NAME] = inv.get(SPOILED_NAME, 0) + qty
                turned = True
                self.pending_food_msgs.append(SPOILAGE_FLAVOR.get(
                    name, f"Your {name} has turned. (Spoiled Food)"))
        if turned:
            p.save()

    def take_food_msgs(self):
        msgs, self.pending_food_msgs = self.pending_food_msgs, []
        return msgs

    def time_label(self):
        """'Day 3, Evening' in town; None while underground (no daylight below)."""
        if self.player.data["position"]["z"] < 0:
            return None
        t = self.player.data.setdefault("time", {"day": 1, "phase": 0, "tick": 0})
        return f"Day {t['day']}, {TIME_PHASES[t['phase']]}"

    def emergence_flavor(self):
        """Prose for walking out of the dungeon and discovering the hour."""
        t = self.player.data.setdefault("time", {"day": 1, "phase": 0, "tick": 0})
        return EMERGENCE_FLAVOR.get(TIME_PHASES[t["phase"]],
                                    "You surface into the ordinary world.")

    def reset_game(self):
        """Abandon the run: restore the pristine character and wipe the
        generated dungeon so the world regenerates fresh on next load."""
        self.player.data = default_player_data()
        self.world.engine_phase = 0
        self.player.refresh_derived_stats()
        self.player.save()
        self.world.rooms = {}
        self.world.save()
        self.turn_count = 0
        self.combat_state = {}
        self.monster_control = {}
        # multiplayer: a "new run" resets the satellites to fresh characters
        # (their slot files are continuously saved, so this is a clean slate).
        for s in self.satellites:
            s.data = default_player_data()
            s.data.setdefault("is_satellite", True)
            s.data.setdefault("down", False)
            s.refresh_derived_stats()
            s.save()
        return self.player.data

    # --- scene / narration context (was an RpgModule tool) ---
    def get_scene_context(self):
        p = self.player.data
        pos = p["position"]
        room_id = f"{pos['x']}_{pos['y']}_{pos['z']}"
        self.world.engine_phase = self.player.data.setdefault(
            "time", {"day": 1, "phase": 0, "tick": 0})["phase"]
        room = self.world.get_room(pos["x"], pos["y"], pos["z"])
        npcs = self._npcs_in_room(room_id)
        monsters = self._normalize_encounter(room.get("encounter"))
        is_dark = (not self.room_is_lit(room)) and not self.player.is_lit()
        sleep_hint = None
        if room_id == INN_ROOM_ID:
            sleep_hint = ("You may sleep here ('sleep') - a full, restful night. "
                          "The Inn Key was the only cost.")

        if is_dark:
            description = DARKNESS_DESCRIPTION
            visual_elements = "Absolute darkness"
            npc_presence = ("You hear something stirring in the dark..."
                            if (npcs or monsters) else "The silence is absolute.")
            # In the dark the player cannot see the room's objects, so we do not
            room_contents = "unseen in the darkness (light a torch and look)"
        else:
            description = room.get("description")
            if not description:
                description = ROOM_TYPE_DESCRIPTIONS.get(room.get("type", "Unknown"), "A dimly lit area.")
            # A cleared Combat room (no live monster) should not keep claiming
            # something lurks - the 'Combat' type is stale once its threat is
            # dead. Any corpse left behind is reported separately in PRESENT.
            if room.get("type") == "Combat" and not monsters:
                description = "A quiet chamber - the danger here has passed."
            if room.get("type") == "Discovery" and room.get("discovery"):
                description = DISCOVERY_DATA.get(room["discovery"], {}).get(
                    "flavor", description)
            elif room.get("atmosphere"):
                description = f"{description} {room['atmosphere']}"
            visual_elements = ", ".join(room.get("visual_tags", [])) if room.get("visual_tags") else "none"
            if npcs and monsters:
                npc_presence = "Present: " + ", ".join([n.name for n in npcs] + monsters)
            elif npcs:
                npc_presence = "NPCs Present: " + ", ".join(n.name for n in npcs)
            elif monsters:
                npc_presence = "Monsters Present: " + ", ".join(monsters)
            else:
                npc_presence = "No NPCs or monsters present."
            for c in self._clear_all_corpses(room_id):
                cstate = ("picked clean" if c.get("searched")
                          else "unsearched - try 'search corpse'")
                npc_presence += (f" | The corpse of the {c.get('monster', 'creature')} "
                                 f"lies here ({cstate}).")
            if room_id == VIRTUE_DATA.get("beggar_room", "-1_0_0"):
                # Aelric the Beggar is a real NPC now (talk works); nudge 'alms'.
                if npc_presence == "No NPCs or monsters present.":
                    npc_presence = "A beggar sits by the garden wall, hat extended - try 'alms'."
                else:
                    npc_presence += " - his hat is extended; 'alms' works here."
            # The authoritative list of what actually sits in the room right now
            # (chest/trap/shrine). This is what stops a treasure room being
            # narrated as 'empty'.
            room_contents = room_features(room)

        discovery_hint = None
        if room.get("type") == "Discovery" and room.get("discovery"):
            discovery_hint = DISCOVERY_DATA.get(room["discovery"], {}).get("verbs")

        shrine_slot = bool(room.get("shrine")) or (room.get("type") == "Shrine")
        return {
            "room_name": room.get("name", "Unknown Location"),
            "description": description,
            "visual_elements": visual_elements,
            "npc_presence": npc_presence,
            "room_contents": room_contents,
            "discovery_hint": discovery_hint,
            "sleep_hint": sleep_hint,
            "shrine_slot": shrine_slot,
            "time_of_day": self.time_label(),
            "lore_grounding": NARRATIVE_CONTEXT,
        }

    # --- movement ---
    def move_player(self, direction):
        self.turn_count += 1
        was_underground = self.player.data["position"]["z"] < 0
        current_pos = self.player.data["position"]
        current_room = self.world.get_room(current_pos["x"], current_pos["y"], current_pos["z"])
        monsters = self._normalize_encounter(current_room.get("encounter"))
        old_last_dir = self.player.data.get("last_direction")
        p_data = self.player.data
        eq = p_data["equipped"]
        fuel = p_data["light_fuel"]

        if "Torch" in eq.values() and fuel["torch"] > 0:
            fuel["torch"] -= 1
            if fuel["torch"] <= 0:
                for slot, item in eq.items():
                    if item == "Torch":
                        eq[slot] = None
        if "Lantern" in eq.values() and fuel["lantern"] > 0:
            fuel["lantern"] -= 1
            if fuel["lantern"] <= 0:
                for slot, item in eq.items():
                    if item == "Lantern":
                        eq[slot] = None
        if p_data["light_duration"] > 0:
            p_data["light_duration"] -= 1
        self._tick_buffs()
        self._tick_all_monster_controls()
        self.player.save()

        cur_room_id = f"{current_pos['x']}_{current_pos['y']}_{current_pos['z']}"
        def _ctrl_of(label):
            return self.monster_control.get(f"{cur_room_id}|{label}", {})
        def _controlled(label):
            c = _ctrl_of(label)
            return (c.get("sleep", 0) > 0 or c.get("hold", 0) > 0
                    or c.get("web", 0) > 0 or c.get("confuse", 0) > 0)
        invisible = (self._buff_active("Invisibility")
                     and not any(self._monster_sees_invisible(m) for m in monsters))
        hostiles = [m for m in monsters if not _controlled(m)]
        if hostiles and not invisible:
            opposites = {"North": "South", "South": "North", "East": "West", "West": "East",
                         "Up": "Down", "Down": "Up"}
            last_dir = self.player.data.get("last_direction")
            attacks = []
            for m in hostiles:
                ar = self.resolve_monster_attack(m)
                if ar["hit"]:
                    self.player.update_hp(-ar["damage"])
                attacks.append(ar)
            first_hit = next((a for a in attacks if a["hit"]), attacks[0])
            if last_dir and direction == opposites.get(last_dir):
                res = self._execute_move(direction)
                if "error" not in res:
                    res["combat_event"] = "escaped"
                    res["monster_attack"] = first_hit
                return res
            else:
                block_msg = "The monsters block your path!"
                if self._buff_active("Invisibility") and any(self._monster_sees_invisible(m) for m in hostiles):
                    seer = next(m for m in hostiles if self._monster_sees_invisible(m))
                    block_msg = f"The {seer} sees through your illusion and blocks your path!"
                return {"error": block_msg,
                        "combat_event": "blocked", "monster_attack": first_hit,
                        "position": current_pos, "room": current_room}

        res = self._execute_move(direction)
        if "error" not in res and invisible and monsters:
            opposites = {"North": "South", "South": "North", "East": "West", "West": "East",
                         "Up": "Down", "Down": "Up"}
            if old_last_dir and direction == opposites.get(old_last_dir):
                # Slipping back the way you came past a hostile = a flee.
                self.player.update_virtue(-1)
                res["control_note"] = (f"You vanish and slip back past the {monsters[0]}, the way "
                                       f"you came. The Protector ran. (Virtue -1)")
            else:
                res["control_note"] = f"You vanish and slip past the {monsters[0]} without a fight."
        elif "error" not in res and any(_controlled(m) for m in monsters):
            which_m = next(m for m in monsters if _controlled(m))
            c = _ctrl_of(which_m)
            which = ("sleeping" if c.get("sleep", 0) > 0
                     else "held" if c.get("hold", 0) > 0
                     else "webbed" if c.get("web", 0) > 0
                     else "confused")
            res["control_note"] = f"You slip past the {which} {which_m} without a fight."
        if "error" not in res:
            if was_underground and res.get("position", {}).get("z", 0) == 0:
                res["emerged"] = True
            self.player.update_satiety(-1)
            if self.player.data["satiety"] <= 0:
                if "Starving" not in self.player.data["status_effects"]:
                    self.player.data["status_effects"].append("Starving")
                self.player.update_hp(-1)
            nz = res.get("position", {}).get("z", 0)
            if nz < 0:
                led = self._virtue_ledger()
                if nz not in led["floors"]:
                    led["floors"].append(nz)
                    self.player.update_virtue(1)
                    res["new_floor"] = True
            np_ = res.get("position", {})
            gone = self.world.decay_drops(np_.get("x", 0), np_.get("y", 0), np_.get("z", 0))
            if gone:
                res["drops_gone"] = gone
        return res

    def _resolve_trap(self, trap_type):
        trap_info = TRAP_DATA.get(trap_type, TRAP_DATA["Pressure Plate (Darts)"])
        save_roll = self.roll_dice(20, self.get_stat_bonus("DEX"))
        if save_roll < trap_info["dc"]:
            damage = trap_info["damage"] + (abs(self.player.data["position"]["z"]) // 2)
            self.player.update_hp(-damage)
            msg = f"The {trap_type} triggered! You take {damage} damage."
            effect = trap_info.get("effect")
            if effect == "poison":
                poison = self._roll_poison_save(trap_info.get("dc", 10))
                if poison.get("applied"):
                    msg += f" The venom burns (CON save failed, roll {poison['roll']} vs DC {poison['dc']})."
                elif poison.get("refreshed"):
                    msg += " The venom burns again (CON save failed)."
                elif poison.get("success"):
                    msg += " You shake off the venom (CON save succeeded)."
            elif effect == "curse":
                if "Cursed" not in self.player.data.get("status_effects", []):
                    self.player.data.setdefault("status_effects", []).append("Cursed")
                    self.player.save()
                msg += " A cold glyph sears your brow - you are CURSED (-1 to hit)."
            return {"triggered": True, "damage": damage, "msg": msg}
        return {"triggered": False, "msg": f"You narrowly avoid the {trap_type}!"}

    def _execute_move(self, direction):
        current_pos = self.player.data["position"]
        current_room = self.world.get_room(current_pos["x"], current_pos["y"], current_pos["z"])
        exits = current_room.get("exits", {})
        opposites = {"North": "South", "South": "North", "East": "West", "West": "East",
                     "Up": "Down", "Down": "Up"}
        last_dir = self.player.data.get("last_direction")

        if direction != "Up" and current_room.get("trap") and direction != opposites.get(last_dir):
            trap_type = current_room.get("trap_type")
            if TRAP_DATA.get(trap_type, {}).get("location") == "Floor":
                res = self._resolve_trap(trap_type)
                if res["triggered"]:
                    room_id = f"{current_pos['x']}_{current_pos['y']}_{current_pos['z']}"
                    if room_id in self.world.rooms:
                        self.world.rooms[room_id]["trap"] = False
                        self.world.save()
                    return {"error": res["msg"], "position": current_pos, "room": current_room}

        # The inn's locked stairway: a consumable Inn Key is the only key that
        # fits (the generic dungeon keys and lockpicks do not).
        if (direction == "Up" and current_pos["x"] == -1 and current_pos["y"] == -1
                and current_pos["z"] == 0 and INN_KEY_ITEM in current_room.get("doors", {})):
            door = current_room["doors"][INN_KEY_ITEM]
            if door.get("state") == "locked":
                inv = self.player.data["inventory"]
                if inv.get(INN_KEY_ITEM, 0) > 0:
                    # The key turns and is consumed; the guest rises to the room.
                    self.player.remove_item(INN_KEY_ITEM, 1)
                    _ages = self.player.data.get("inn_key_ages")
                    if _ages:
                        _ages.pop(0)
                    door["state"] = "closed"
                    self.player.data["inn_key"] = self.player.data["satiety"]
                    self.player.data["inn_slept"] = False
                    self.player.save()
                    self._save_room(INN_BASE_ID)
                    tx, ty, tz = -1, -1, 1
                    self._inn_unlock_msg = True
                else:
                    return {"error": "The stairway door is locked. Only the inn's own "
                                     "key will open it - ask the barkeep ('shop').",
                            "position": current_pos, "room": current_room}
            # open: go up
            tx, ty, tz = -1, -1, 1
        elif direction in current_room.get("doors", {}):
            door = current_room["doors"][direction]
            if door.get("state") == "locked":
                return {"error": f"The door to the {direction} is locked. "
                                 "('open to try - a key or lockpick may serve.')",
                        "position": current_pos, "room": current_room}
            return {"error": f"A closed door stands to the {direction}. Try 'open {direction.lower()}'.",
                    "position": current_pos, "room": current_room}
        elif direction in current_room.get("obstacles", {}):
            ob = current_room["obstacles"][direction]
            if ob.get("name", "") in self.GAP_OBSTACLES and self._buff_active("Fly", "Levitate"):
                # Fly/Levitate carry you across the gap - route through the far room.
                target_id = ob["to"]
                tx, ty, tz = map(int, target_id.split("_"))
            else:
                return {"error": f"The {ob.get('name', 'obstacle')} bars the way to the {direction}.",
                        "position": current_pos, "room": current_room}
        elif direction in exits:
            target_id = exits[direction]
            tx, ty, tz = map(int, target_id.split("_"))
        elif direction == "Up" and current_room.get("stairs_up"):
            if current_pos["z"] == -1:
                tx, ty, tz = 0, -1, 0
            else:
                tx, ty, tz = current_pos["x"], current_pos["y"], current_pos["z"] + 1
        elif direction == "Down" and current_room.get("stairs_down"):
            tx, ty, tz = current_pos["x"], current_pos["y"], current_pos["z"] - 1
            target_room = self.world.get_room(tx, ty, tz)
            if not target_room.get("stairs_up"):
                # The shaft itself is always walkable: the floor below always
                # has stairs leading back up, even though its *marker room*
                # may be elsewhere on the map.
                tkey = f"{tx}_{ty}_{tz}"
                if tkey in self.world.rooms:
                    self.world.rooms[tkey]["stairs_up"] = True
                    self.world.rooms[tkey].setdefault("visual_tags", []).append(
                        "stone stairs leading up")
                    self.world.save()
        else:
            return {"error": "You cannot go that way."}

        # Leaving the inn's guest room: if the guest slept and the stairway
        # stands open, it re-locks behind them (the door lives on the base inn).
        if (current_pos["x"] == -1 and current_pos["y"] == -1 and current_pos["z"] == 1
                and (tx, ty, tz) != (-1, -1, 1)
                and self.player.data.get("inn_slept")):
            door = self._inn_door()
            if door is not None and door.get("state") == "closed":
                door["state"] = "locked"
                self.player.data["inn_key"] = None
                self.player.data["inn_slept"] = False
                self.player.save()
                self._save_room(INN_BASE_ID)

        self.player.data["position"] = {"x": tx, "y": ty, "z": tz}
        self.player.data["last_direction"] = direction
        self.player.save()
        room = self.world.get_room(tx, ty, tz)
        res = {"position": self.player.data["position"], "room": room,
               "satiety": self.player.data["satiety"],
               "wandering_check": (self.turn_count % 10 == 0), "lore_grounding": NARRATIVE_CONTEXT}
        if getattr(self, "_inn_unlock_msg", False):
            res["inn_key_unlock"] = True
            self._inn_unlock_msg = False
        return res

    # --- town / shops ---
    def get_town_map_context(self):
        return self.world.town_rooms

    def get_shop_inventory(self, shop_name):
        inventories = _load_json("shop_inventory.json", {})
        if not inventories:
            return {"error": "Shop inventory database not found."}
        if shop_name not in inventories:
            return {"error": f"No inventory records found for {shop_name}."}
        shop_data = inventories[shop_name]
        detailed_inventory = {}
        ledger = self.world.ledger if hasattr(self.world, "ledger") else {}
        for category_label, ledger_key in shop_data.items():
            table = ledger.get(ledger_key, {})
            category_list = []
            for item, info in table.items():
                details = info if isinstance(info, dict) else {}
                if details.get("tradeable", True) is False:
                    continue
                price = LEDGER_DATA.get(item, "Unknown Price")
                category_list.append({"item": item, "price": price, "details": details})
            detailed_inventory[category_label] = category_list
        return detailed_inventory

    def _get_current_shop(self):
        pos = self.player.data["position"]
        room_id = f"{pos['x']}_{pos['y']}_{pos['z']}"
        room = self.world.get_room(pos["x"], pos["y"], pos["z"])
        inventories = _load_json("shop_inventory.json", {})
        # A dedicated shop room is keyed by its own name...
        shop_name = room.get("name")
        shop_data = inventories.get(shop_name)
        if shop_data:
            return shop_name, shop_data
        # ...but a street merchant (Slippery Pete) shares a public room, so
        for npc in self._npcs_in_room(room_id):
            if getattr(npc, "shop", None) and npc.shop in inventories:
                return npc.shop, inventories[npc.shop]
        return shop_name, None
        return shop_name, None

    def trade_item(self, item_name, action="sell", qty=1):
        action = (action or "sell").strip().lower()
        if action not in ("buy", "sell"):
            return {"success": False, "message": f"Unknown action '{action}'. Use 'buy' or 'sell'."}
        try:
            qty = max(1, int(qty))
        except (TypeError, ValueError):
            qty = 1
        shop_name, shop_data = self._get_current_shop()
        if not shop_data:
            return {"success": False, "message": "There is no merchant here to trade with."}
        ledger = getattr(self.world, "ledger", {})
        shop_category = None
        for label, key in shop_data.items():
            if item_name in ledger.get(key, {}):
                shop_category = key
                break
        price = LEDGER_DATA.get(item_name)
        if action == "buy":
            if price is None or shop_category is None:
                return {"success": False, "message": f"{shop_name} doesn't deal in {item_name}."}
            ledger_info = ledger.get(shop_category, {}).get(item_name)
            if isinstance(ledger_info, dict) and ledger_info.get("tradeable", True) is False:
                return {"success": False, "message": f"{shop_name} doesn't stock {item_name}."}
            total = price * qty
            if self.player.data["gold"] < total:
                return {"success": False, "message": f"You can't afford that. {total} gold needed."}
            inv_before = dict(self.player.data["inventory"])
            result = self.player.add_item(item_name, qty)
            if not result.get("success"):
                return result
            self.player.update_gold(-total)
            if item_name == INN_KEY_ITEM:
                # A key is only "fresh" for a 50-tick window (anti-stockpile).
                self.player.data.setdefault("inn_key_ages", []).extend(
                    [self.player.data["satiety"]] * qty)
                self.player.save()
            message = f"You bought {qty}x {item_name} for {total} gold."
            if item_name in ("Sack", "Reagent Pouch"):
                # Warn if the shared max-2 sack slot pool displaced a container.
                old_cap = self.player.get_inventory_capacity(inv_before)
                new_cap = self.player.get_inventory_capacity(self.player.data["inventory"])
                sacks_held = inv_before.get("Sack", 0)
                pouches_held = inv_before.get("Reagent Pouch", 0)
                if new_cap < old_cap:
                    message += (" Careful - it claims a sack slot, and one of your sacks "
                                "no longer adds capacity!")
                elif new_cap == old_cap and (sacks_held + pouches_held) >= 2:
                    message += " Careful - the sack slots are full, so this adds no capacity!"
            return {"success": True,
                    "message": message,
                    "gold": self.player.data["gold"]}
        owned = self.player.data["inventory"].get(item_name, 0)
        if owned <= 0:
            return {"success": False, "message": f"You don't have any {item_name} to sell."}
        # Compare normalized: an equipped "Torch" came from a "Torches (6)" key.
        worn = {normalize_item_name(v) for v in self.player.data["equipped"].values() if v}
        if normalize_item_name(item_name) in worn:
            return {"success": False, "message": f"You have {item_name} equipped. Unequip it first."}
        if price is None or shop_category is None:
            return {"success": False, "message": f"{shop_name} doesn't deal in {item_name}."}
        ledger_info = ledger.get(shop_category, {}).get(item_name)
        if isinstance(ledger_info, dict) and ledger_info.get("tradeable", True) is False:
            return {"success": False, "message": f"{shop_name} won't take {item_name} off your hands."}
        qty = min(qty, owned)
        cha_mod = self.get_stat_bonus("CHA")
        value = max(0, (price // 2) * qty + cha_mod)
        result = self.player.remove_item(item_name, qty)
        if not result.get("success"):
            return result
        self.player.update_gold(value)
        return {"success": True, "message": f"You sold {qty}x {item_name} for {value} gold.",
                "gold": self.player.data["gold"]}

    def get_stat_bonus(self, stat_name, player=None):
        p = player if player is not None else self.player
        return p.data["stats"].get(stat_name, 10) - 10

    def _find_monster(self, monster_name):
        bestiary = self.world.generator.bestiary
        if not monster_name:
            return None
        # A multi-monster label like 'Goblin 2' resolves to the bestiary entry 'Goblin'.
        _m = re.match(r"^(.*\S)\s+(\d+)$", monster_name.strip())
        if _m:
            monster_name = _m.group(1)
        if monster_name in bestiary:
            return monster_name

        def get_words(s):
            return set(re.findall(r"\w+", s.lower()))

        search_words = get_words(monster_name)
        for key in bestiary:
            if search_words == get_words(key):
                return key
        return None

    # --- Multi-actor room support ---------------------------------------- #
    # A room may hold more than one monster and more than one NPC. Monsters
    # are stored in room['encounter'] as a list (a bare string is normalized
    # on read); NPCs are stored in room['npcs'] as a list of NPC_REGISTRY
    # keys. combat_state and monster_control are keyed by room_id|label so
    # each actor tracks its own HP and control effects.
    def _normalize_encounter(self, enc):
        """The room's live monsters as a list (bare 'None'/'None'-string -> [])."""
        if enc is None:
            return []
        if isinstance(enc, str):
            return [enc] if enc and enc != "None" else []
        if isinstance(enc, (list, tuple)):
            return [m for m in enc if m and m != "None"]
        return []

    def _encounter_list(self, room_id=None):
        """Live monster list for a room (current room by default)."""
        if room_id is None:
            room_id = self._room_id()
        room = self._authoritative_room(room_id)
        return self._normalize_encounter((room or {}).get("encounter"))

    def _clear_room_monster(self, room_id, label):
        """Remove one monster (by label) from a room and persist."""
        room = self._authoritative_room(room_id)
        if room is None:
            return False
        enc = self._normalize_encounter(room.get("encounter"))
        if label in enc:
            enc.remove(label)
            room["encounter"] = enc
            self._save_room(room_id)
        return True

    def _clear_room_encounter(self, room_id):
        """Remove ALL monsters from a room (dungeon or town) and persist."""
        self._clear_monster_control(room_id)
        cleared = False
        room = self._authoritative_room(room_id)
        if room is not None and room.get("encounter") not in (None, "None", []):
            room["encounter"] = []
            self._save_room(room_id)
            cleared = True
        return cleared

    def _remove_npc_from_room(self, room_id, key):
        """Remove one NPC (by registry key) from a room and persist."""
        room = self._authoritative_room(room_id)
        if room is None:
            return False
        npcs = room.get("npcs")
        if isinstance(npcs, list) and key in npcs:
            npcs.remove(key)
            room["npcs"] = npcs
            self._save_room(room_id)
        return True

    def _monster_control_for(self, room_id, label):
        """Per-actor control state (room_id|label)."""
        key = f"{room_id}|{label}"
        if key not in self.monster_control:
            self.monster_control[key] = {"sleep": 0, "hold": 0, "web": 0, "confuse": 0}
        return self.monster_control[key]

    def _clear_monster_control(self, room_id, label=None):
        """Clear control for one actor (label) or every actor in the room."""
        if label is None:
            for key in [k for k in self.monster_control if k.split("|")[0] == room_id]:
                self.monster_control.pop(key, None)
        else:
            self.monster_control.pop(f"{room_id}|{label}", None)

    def _clear_all_corpses(self, room_id):
        """Every corpse dict in a room (a list, or a single dict, or none)."""
        room = self._authoritative_room(room_id)
        if room is None:
            return []
        c = room.get("corpse")
        if c is None:
            return []
        return c if isinstance(c, list) else [c]

    def _deposit_corpse(self, room_id, monster_name, loot):
        """Leave a searchable corpse holding the kill's loot in the room.
        A room may hold several corpses (one per slain monster). Returns a
        one-line flavour message for the combat report."""
        room = self._authoritative_room(room_id)
        if room is None:
            return f"The {monster_name}'s corpse lies here - search it for spoils."
        corpse = {"monster": monster_name, "loot": loot, "searched": False}
        existing = room.get("corpse")
        if existing is None:
            room["corpse"] = [corpse]
        elif isinstance(existing, list):
            existing.append(corpse)
        else:
            room["corpse"] = [existing, corpse]
        self._save_room(room_id)
        return f"The {monster_name}'s corpse lies here - search it for spoils."

    def _name_is_corpse(self, name, corpse):
        """True if `name` refers to this corpse (an alias, the monster's own
        name, or nothing at all - a bare 'search' means the corpse)."""
        low = (name or "").strip().lower()
        if not low:
            return True
        if any(a in low for a in self.CORPSE_ALIASES):
            return True
        monster = (corpse.get("monster") or "").lower()
        return bool(monster) and (low in monster or monster in low)

    def search_corpse(self, name=None):
        """Loot a corpse left by a slain monster in the current room. A bare
        'search' loots the only corpse (or the first unsearched one); naming a
        monster loots that specific corpse. Takes its gold and items, then
        marks it picked clean (leftovers stay behind for a fuller pack)."""
        room_id = self._room_id()
        corpses = [c for c in self._clear_all_corpses(room_id) if c]
        if not corpses:
            return "There is no corpse here to search."
        low = (name or "").strip().lower()
        named = bool(low) and not any(a in low for a in self.CORPSE_ALIASES)
        if named:
            targets = [c for c in corpses if self._name_is_corpse(name, c)]
            if not targets:
                mons = ", ".join(c.get("monster", "it") for c in corpses)
                return f"There is no {name} here - only the corpse(s) of: {mons}."
        else:
            unsearched = [c for c in corpses if not c.get("searched")]
            targets = unsearched if unsearched else [corpses[0]]
        taken_all, notes_all = [], []
        for corpse in targets:
            if corpse.get("searched"):
                notes_all.append(f"The corpse of the {corpse.get('monster', 'it')} has already been picked clean.")
                continue
            loot = corpse.get("loot") or {}
            gold = int(loot.get("gold", 0) or 0)
            items = [list(e) for e in (loot.get("items", []) or [])]
            taken, notes, leftovers = [], [], []
            if gold > 0:
                self.player.update_gold(gold)
                taken.append(f"{gold} gold")
            for entry in items:
                item_name = entry[0]
                qty = entry[1] if len(entry) > 1 else 1
                res = self.player.add_item(item_name, qty)
                if res.get("success"):
                    taken.append(item_name + (f" x{qty}" if qty > 1 else ""))
                else:
                    leftovers.append(entry)
                    notes.append(res.get("message", f"You cannot carry the {item_name}."))
            corpse["loot"] = {"gold": 0, "items": leftovers}
            corpse["searched"] = not leftovers
            if taken:
                if len(taken) == 1:
                    head = f"You search the corpse of the {corpse.get('monster', 'it')} and take {taken[0]}."
                else:
                    head = (f"You search the corpse of the {corpse.get('monster', 'it')} and take "
                            + ", ".join(taken[:-1]) + " and " + taken[-1] + ".")
            elif leftovers:
                head = f"You search the corpse of the {corpse.get('monster', 'it')}, but your hands are too full."
            else:
                head = f"You search the corpse of the {corpse.get('monster', 'it')}, but it yields nothing you can carry."
            taken_all.append(head)
            notes_all.extend(notes)
        self._save_room(room_id)
        return " ".join(taken_all + notes_all)

    DARK_ROOM_TAGS = ("oppressive darkness", "darkness")
    GAP_OBSTACLES = ("Broken Floor",)
    BASH_OBSTACLES = ("Cave-in", "Heavy Portcullis")

    def room_is_lit(self, room=None):
        """True if the room itself provides light (town, torch-lit dungeon).
        Mirrors the combat darkness rule: only 'oppressive darkness' rooms are dark."""
        if room is None:
            pos = self.player.data["position"]
            room = self.world.get_room(pos["x"], pos["y"], pos["z"])
        tags = room.get("visual_tags") or []
        return not any(t in self.DARK_ROOM_TAGS for t in tags)

    def calculate_ac(self, player=None):
        p = player if player is not None else self.player
        poly = self._polymorph_active(p)
        if poly:
            return poly.get("ac", 10)
        armor_bonus = (ARMOR_DATA[p.data["equipped"]["armor"]]["ac"]
                       if p.data["equipped"]["armor"] in ARMOR_DATA else 0)
        shield_bonus = (ARMOR_DATA[p.data["equipped"]["shield"]]["ac"]
                        if p.data["equipped"]["shield"] in ARMOR_DATA else 0)
        dex = p.data["stats"].get("DEX", 10)
        buff_ac = 0
        for b in p.data.get("buffs", []):
            buff_ac += b.get("tags", {}).get("ac_bonus", 0)
        return armor_bonus + shield_bonus + dex + buff_ac

    def calculate_attack_bonus(self, weapon_name, player=None):
        p = player if player is not None else self.player
        cursed = -1 if "Cursed" in p.data.get("status_effects", []) else 0
        poly = self._polymorph_active(p)
        if poly:
            return poly.get("atk", 0) + cursed
        weapon_stats = WEAPON_DATA.get(weapon_name, {})
        w_type = weapon_stats.get("type", "melee")
        w_mod = weapon_stats.get("mod", 0)
        buff_atk = 0
        for b in p.data.get("buffs", []):
            buff_atk += b.get("tags", {}).get("attack_bonus", 0)
        return (self.get_stat_bonus("DEX", p) if w_type == "ranged" else self.get_stat_bonus("STR", p)) + w_mod + buff_atk + cursed

    def resolve_attack(self, attacker_weapon, defender_ac, penalty=0, player=None):
        p = player if player is not None else self.player
        attack_roll = self.roll_dice(20, self.calculate_attack_bonus(attacker_weapon, p)) + penalty
        if attack_roll >= defender_ac:
            weapon_stats = WEAPON_DATA.get(attacker_weapon, {"die": 6, "mod": 0})
            damage = random.randint(1, weapon_stats["die"]) + self.get_stat_bonus("STR", p) + weapon_stats.get("mod", 0)
            return {"hit": True, "damage": max(1, damage), "roll": attack_roll}
        return {"hit": False, "damage": 0, "roll": attack_roll}

    def _roll_poison_save(self, dc=10, player=None):
        p = player if player is not None else self.player
        con_bonus = self.get_stat_bonus("CON", p)
        roll = self.roll_dice(20, con_bonus)
        success = roll >= dc
        already_poisoned = "Poisoned" in p.data.get("status_effects", [])
        applied = False
        refreshed = False
        if not success:
            if not already_poisoned:
                p.data.setdefault("status_effects", []).append("Poisoned")
                p.data["poison_duration"] = 3
                applied = True
            else:
                p.data["poison_duration"] = max(int(p.data.get("poison_duration", 0)), 2)
                refreshed = True
        p.save()
        return {"roll": roll, "bonus": con_bonus, "dc": dc, "success": success,
                "applied": applied, "refreshed": refreshed, "already_poisoned": already_poisoned}

    def resolve_monster_attack(self, monster_name, penalty=0, player=None):
        p = player if player is not None else self.player
        monster_id = self._find_monster(monster_name)
        monster = self.world.generator.bestiary.get(monster_id, {})
        if not monster:
            return {"hit": False, "damage": 0, "message": "The creature is confused and misses!"}
        player_ac = self.calculate_ac(p)
        attack_bonus = monster.get("atk", monster.get("attack_bonus", 0)) + penalty
        roll = random.randint(1, 20) + attack_bonus
        who = "you" if p is self.player else p.slot
        if roll >= player_ac:
            dmg_die = monster.get("damage_die", 6)
            dmg_mod = monster.get("damage_mod", 0)
            damage = max(1, random.randint(1, dmg_die) + dmg_mod)
            result = {"hit": True, "damage": damage, "roll": roll, "target": p,
                      "message": f"The {monster_name} hits {who} for {damage} damage."}
            if "poison" in monster.get("special", []):
                poison_save = self._roll_poison_save(dc=monster.get("poison_dc", 10), player=p)
                result["poison_save"] = poison_save
                if poison_save["applied"] and not poison_save["already_poisoned"]:
                    result["message"] += (f" {who.title()} feel venom sink into your wound. {who.title()} fail your CON save "
                                          f"({poison_save['roll']} vs DC {poison_save['dc']}) and are poisoned.")
                elif poison_save["applied"] or poison_save["refreshed"]:
                    result["message"] += (f" Fresh venom wracks {who}. {who.title()} fail your CON save "
                                          f"({poison_save['roll']} vs DC {poison_save['dc']}) and your poison lingers.")
                else:
                    result["message"] += (f" {who.title()} feel venom sink into your wound, but pass your CON save "
                                          f"({poison_save['roll']} vs DC {poison_save['dc']}).")
            return result
        return {"hit": False, "damage": 0, "roll": roll, "target": p,
                "message": f"The {monster_name} misses."}

    def _clear_poison(self, player=None):
        p = player if player is not None else self.player
        p.data["poison_duration"] = 0
        p.data["status_effects"] = [
            e for e in p.data.get("status_effects", []) if e != "Poisoned"
        ]
        p.save()

    def _tick_poison(self, player=None):
        p = player if player is not None else self.player
        if "Poisoned" not in p.data.get("status_effects", []):
            return None
        is_sat = p is not self.player
        who = "you" if not is_sat else p.slot
        damage = 1
        hp_result = p.update_hp(-damage)
        if isinstance(hp_result, str):
            # the Caller died (satellites route to 'down' and return 0)
            self._clear_poison(p)
            return {"damage": damage, "remaining": 0, "player_died": True,
                    "target": p, "death_message": hp_result, "message": "The poison finishes you."}
        if hp_result <= 0:
            # a satellite dropped to 0: it is 'down' for the session (C5)
            self._clear_poison(p)
            return {"damage": damage, "remaining": 0, "player_died": False,
                    "sat_down": True, "target": p,
                    "message": f"The poison finishes {who} - {who} go down."}
        duration = int(p.data.get("poison_duration", 1)) - 1
        if duration <= 0:
            self._clear_poison(p)
            return {"damage": damage, "remaining": 0, "player_died": False, "target": p,
                    "message": f"The poison burns through {who} and fades. (-1 HP)"}
        p.data["poison_duration"] = duration
        p.save()
        return {"damage": damage, "remaining": duration, "player_died": False, "target": p,
                "message": f"The poison courses through {who}. (-1 HP, {duration} round(s) left)"}

    def apply_monster_special(self, monster_name):
        monster_id = self._find_monster(monster_name)
        monster = self.world.generator.bestiary.get(monster_id, {})
        specials = monster.get("special", [])
        for trait in specials:
            if trait == "poison":
                return "You feel a burning toxin entering your veins! (Poisoned)"
            if trait == "disease":
                self.player.data["max_hp"] = 4
                self.player.update_hp(-self.player.data["hp"] + 4)
                effs = self.player.data.setdefault("status_effects", [])
                if "Diseased" not in effs:
                    effs.append("Diseased")
                self.player.save()
                return "A sickening malaise takes hold of you. (Diseased - max HP reduced to 4)"
            if trait == "petrify":
                return self.player.die()
            if trait == "steal_food":
                self.player.update_satiety(-(self.player.data["satiety"] // 2))
                return "The Gremlin nimbly steals half your rations!"
        return None

    def mix_reagents(self, formula):
        if isinstance(formula, str):
            try:
                formula = json.loads(formula)
            except json.JSONDecodeError:
                return {"success": False,
                        "message": "The reagents are mixed in a nonsensical order. Nothing happens."}
        for item, qty in formula.items():
            self.player.remove_item(item, qty)
        cast_spell = None
        for spell_name, data in SPELL_DATA.items():
            if data["reagents"] == formula:
                cast_spell = spell_name
                break
        if not cast_spell:
            return {"success": False,
                    "message": "The mixture bubbles violently and evaporates into a foul-smelling cloud. Nothing happened."}
        effects = {
            "Cure Light Wounds": lambda: self.player.update_hp(4),
            "Cure Serious Wounds": lambda: self.player.update_hp(10),
            "Cure Poison": lambda: self._clear_poison(),
            "Light": lambda: self._apply_light_spell(),
            "Protection": lambda: "Your skin takes on a faint metallic sheen. (AC increased temporarily - handled by AI)",
            "Sleep": lambda: self._spell_sleep(),
            "Hold Person": lambda: self._spell_hold_person(),
            "Web": lambda: self._spell_web(),
            "Charm Person": lambda: self._spell_charm_person(),
            "Charm Monster": lambda: self._spell_charm_monster(),
            "Confusion": lambda: self._spell_confusion(),
            "Detect Magic": lambda: self.detect_magic(),
            "Purify Food": lambda: self._purify_food(),
            "Fireball": lambda: self._spell_fireball(),
            "Lightning Bolt": lambda: self._spell_lightning_bolt(),
            "Wall of Fire": lambda: self._spell_wall_of_fire(),
            "Disintegrate": lambda: self._spell_disintegrate(),
            "Kill": lambda: self._spell_kill(),
            "Lock": lambda: self._spell_lock(),
            "Knock": lambda: self._spell_knock(),
            "Levitate": lambda: self._spell_levitate(),
            "Fly": lambda: self._spell_fly(),
            "Invisibility": lambda: self._spell_invisibility(),
            "Water Breathing": lambda: self._spell_water_breathing(),
            "Passwall": lambda: self._spell_passwall(),
            "Teleport": lambda: self._spell_teleport(),
            "Locate Object or Person": lambda: self._spell_locate(),
            "Continuous Light": lambda: self._spell_continuous_light(),
            "Remove Curse": lambda: self._spell_remove_curse(),
            "Dispel Magic": lambda: self._spell_dispel_magic(),
            "Find Traps": lambda: self._spell_find_traps(),
            "Polymorph": lambda: self._spell_polymorph(),
            "Commune with the Dead": lambda: self._spell_commune(),
            "Telekinesis": lambda: self._spell_telekinesis(),
            "Speak with Plants": lambda: self._spell_speak_with_plants(),
            "Speak with Animals": lambda: self._spell_speak_with_animals(),
            "Wish": lambda: self._spell_wish(),
        }
        if cast_spell in effects:
            result = effects[cast_spell]()
            if isinstance(result, dict):
                return result
            if isinstance(result, int):
                return {"success": True, "spell": cast_spell, "message": f"Success! {cast_spell} cast. HP updated."}
            return {"success": True, "spell": cast_spell, "message": result}
        return {"success": True, "spell": cast_spell,
                "message": "The spell manifests successfully. (Resolution is prompt-based; call supporting game effects if needed)."}

    def cast_by_name(self, spell_name):
        """Cast a spell whose formula the player knows, paying the reagents
        straight out of the pack (no menu)."""
        if not spell_name:
            return {"success": False, "message": "Cast which spell?"}
        name = None
        low = spell_name.strip().lower()
        for known in SPELL_DATA:
            if known.lower() == low:
                name = known
                break
        if not name:
            hits = [k for k in SPELL_DATA if low in k.lower()]
            if len(hits) == 1:
                name = hits[0]
            elif len(hits) > 1:
                return {"success": False,
                        "message": "Which spell? " + ", ".join(sorted(hits))}
            else:
                return {"success": False,
                        "message": f"You know no spell called '{spell_name}'."}
        need = SPELL_DATA[name]["reagents"]
        inv = self.player.data["inventory"]
        short = [f"{q}x {r} (have {inv.get(r, 0)})" for r, q in need.items()
                 if inv.get(r, 0) < q]
        if short:
            return {"success": False,
                    "message": f"You lack the reagents for {name}: " + ", ".join(short)}
        return self.mix_reagents(dict(need))

    def use_item(self, item_name):
        inv = self.player.data["inventory"]
        match, ambiguous = resolve_item_name(
            item_name, [k for k, q in inv.items() if q > 0])
        if ambiguous:
            return {"success": False,
                    "message": "Which one - " + " or ".join(ambiguous) + "?"}
        if not match:
            return {"success": False, "message": f"You don't have a {item_name}."}
        item_name = match
        usable_items = {
            "Red Potion": {"spell": "Cure Light Wounds", "effect": lambda: self.player.update_hp(4),
                           "flavor": "You uncork the vial and drain its crimson contents. Warmth floods your veins as knitted flesh closes your wounds."},
            "Green Potion": {"spell": "Cure Poison", "effect": lambda: self._clear_poison(),
                             "flavor": "You down the bitter green liquid. It burns going down, but the sickly sweetness of poison fades from your blood."},
            "Blue Potion": {"spell": "Cure Disease", "effect": lambda: self._cure_disease(),
                            "flavor": "The cool blue tonic soothes you from throat to stomach. The feverish haze of disease lifts."},
            "Rations, Preserved": {"spell": None, "effect": lambda: self.player.update_satiety(50),
                                   "flavor": "You chew through the salted, dried fare. Not gourmet, but it fills the void. (+50 Satiety)"},
            "Rations (meal in town)": {"spell": None, "effect": self._eat_hot_meal,
                                       "flavor": "You eat the meal while it is still worth eating."},
            "Spoiled Food": {"spell": None, "effect": lambda: self.player.update_satiety(10),
                             "flavor": "You eat it anyway. It was a bold choice. (+10 Satiety)"},
            "Monster Meat": {"spell": None, "effect": self._eat_raw_meat,
                             "flavor": "You eat the meat raw, because you are hungry enough to."},
        }
        # Readables (pages/scraps/books) are read, not used - nothing is consumed.
        if item_name in READABLE_DATA:
            return self.read_item(item_name)
        use_key = (item_name if item_name in usable_items
                   else find_table_key(item_name, usable_items))
        if not use_key:
            return {"success": False, "message": f"The {item_name} doesn't seem to have any obvious use."}
        self.player.remove_item(item_name, 1)
        item_data = usable_items[use_key]
        hp_before = self.player.data["hp"]
        result = item_data["effect"]()
        response = {"success": True, "item": item_name, "message": item_data["flavor"]}
        if isinstance(result, str) and result:
            response["message"] += " " + result
        if item_data["spell"]:
            response["spell"] = item_data["spell"]
        if isinstance(result, int):
            response["hp_change"] = result - hp_before
            response["hp_total"] = result
        if item_data["spell"] == "Cure Disease":
            p = self.player.data
            response["message"] += (f" Your health returns fully to "
                                    f"{p['hp']}/{p['max_hp']}.")
        return response

    def read_item(self, item_name):
        """Read a carried readable (page/scrap/book). Not consumed.

        A bare call (empty name) lists the readables carried. The game never
        tracks what you have learned (Ultima-style - the player IS the
        Protector): reading only shows the text so you can commit it to
        memory or write it down.
        """
        inv = self.player.data["inventory"]
        carried = [k for k, q in inv.items() if q > 0 and k in READABLE_DATA]
        if not (item_name or "").strip():
            if not carried:
                return {"success": False,
                        "message": "You carry nothing to read."}
            return {"success": True, "readables": sorted(carried),
                    "message": "You carry: " + ", ".join(sorted(carried)) +
                               ". (read <item> to look one over)"}
        match, ambiguous = resolve_item_name(item_name, carried)
        if ambiguous:
            return {"success": False,
                    "message": "Which one - " + " or ".join(ambiguous) + "?"}
        if not match:
            return {"success": False,
                    "message": f"You don't have a {item_name} to read."}
        info = READABLE_DATA[match]
        return {"success": True, "item": match, "kind": info.get("kind"),
                "text": info.get("text", ""),
                "message": info.get("text", f"The {match} holds no legible text.")}

    def _eat_hot_meal(self):
        p = self.player.data
        gain = max(0, p["max_satiety"] - p["satiety"])
        self.player.update_satiety(gain)
        return f"(+{gain} Satiety)"

    def _eat_raw_meat(self):
        p = self.player.data
        self.player.update_satiety(10)
        if random.random() < 0.2:
            return self._apply_disease()
        return "(+10 Satiety)"

    def _purify_food(self):
        """Purify Food: Spoiled Food / Monster Meat -> Rations, Preserved."""
        inv = self.player.data["inventory"]
        found = {n: inv.get(n, 0) for n in (SPOILED_NAME, "Monster Meat") if inv.get(n, 0) > 0}
        if not found:
            return {"success": True, "spell": "Purify Food",
                    "message": "You purify nothing in particular. (Spoiled Food and Monster Meat keep best.)"}
        converted = 0
        for name, qty in found.items():
            inv.pop(name, None)
            self.player.data.get("item_ages", {}).pop(name, None)
            for _ in range(qty):
                if self.player.add_item("Rations, Preserved").get("success"):
                    converted += 1
                else:
                    self.player.add_item(name)  # no room; put the rest back
                    break
        return {"success": True, "spell": "Purify Food",
                "message": f"The food steams, then smells honestly of itself again. "
                           f"({converted}x Rations, Preserved)"}

    def _apply_light_spell(self):
        self.player.data["light_duration"] = 50
        self.player.save()
        return "A sphere of soft, magical light erupts around you, illuminating the darkness for some time."

    # --- spell damage & buff infrastructure (sub-phase 2d, batch 1) ---
    def _int_bonus(self):
        return self.player.data["stats"].get("INT", 10) - 10

    def _apply_buff(self, name, turns, tags=None):
        buffs = self.player.data.setdefault("buffs", [])
        for b in buffs:
            if b.get("name") == name:
                b["turns"] = max(b.get("turns", 0), int(turns))
                b["tags"] = tags or b.get("tags", {})
                self.player.save()
                return
        buffs.append({"name": name, "turns": int(turns), "tags": tags or {}})
        self.player.save()

    def _get_buff(self, name):
        for b in self.player.data.get("buffs", []):
            if b.get("name") == name:
                return b
        return None

    def _tick_buffs(self):
        """Decrement buff durations on a normal (movement) turn; drop expired."""
        buffs = self.player.data.get("buffs", [])
        if not buffs:
            return []
        lines = []
        kept = []
        for b in buffs:
            b["turns"] = b.get("turns", 0) - 1
            if b["turns"] <= 0:
                lines.append(f"{b['name']} fades.")
            else:
                kept.append(b)
        self.player.data["buffs"] = kept
        if lines:
            self.player.save()
        return lines

    def _room_encounter(self):
        """Live monsters in the current room, as a list (may be empty)."""
        return self._encounter_list()

    def _spell_damage_encounter(self, monster_name, damage, no_corpse=False, source=""):
        """Apply spell damage to one monster; handle death/corpse/virtue (per-actor)."""
        room_id = self._room_id()
        bestiary = self.world.generator.bestiary
        mdata = bestiary.get(self._find_monster(monster_name) or monster_name, {})
        ckey = f"{room_id}|{monster_name}"
        if ckey not in self.combat_state:
            self.combat_state[ckey] = mdata.get("hp", 10)
        self.combat_state[ckey] -= damage
        dead = self.combat_state[ckey] <= 0
        msg = f"{source}The {monster_name} takes {damage} damage."
        if dead:
            self._clear_room_monster(room_id, monster_name)
            self._clear_monster_control(room_id, monster_name)
            self.combat_state.pop(ckey, None)
            self.player.update_virtue(2)
            if not no_corpse:
                self._deposit_corpse(room_id, monster_name, self.roll_monster_drops(monster_name))
            msg += f" The {monster_name} is destroyed!"
        return {"success": True, "damage": damage, "monster_dead": dead, "message": msg}

    def _spell_fireball(self):
        X = self._int_bonus()
        enc = self._room_encounter()
        if not enc:
            return {"success": True, "spell": "Fireball",
                    "message": "The fireball erupts in a wash of light - but there is nothing there to burn."}
        dice = max(0, X)
        dmg = sum(random.randint(1, 6) for _ in range(dice)) + dice
        res = self._spell_damage_encounter(enc[0], dmg, source="A roaring fireball engulfs the room. ")
        res["spell"] = "Fireball"
        return res

    def _spell_lightning_bolt(self):
        X = self._int_bonus()
        enc = self._room_encounter()
        if not enc:
            return {"success": True, "spell": "Lightning Bolt",
                    "message": "The bolt crackles and dies in the air - nothing to strike."}
        bestiary = self.world.generator.bestiary
        mdata = bestiary.get(enc[0], {})
        roll = self.roll_dice(20, 0)
        if roll < mdata.get("ac", 10):
            return {"success": True, "spell": "Lightning Bolt",
                    "message": f"A lightning bolt arcs toward the {enc[0]} and misses. (roll {roll} vs AC {mdata.get('ac',10)})"}
        dice = max(0, X)
        dmg = sum(random.randint(1, 8) for _ in range(dice)) + dice
        res = self._spell_damage_encounter(enc[0], dmg, source="A bolt of lightning lashes down. ")
        res["spell"] = "Lightning Bolt"
        res["message"] = f"(roll {roll}) " + res["message"]
        return res

    def _spell_wall_of_fire(self):
        X = self._int_bonus()
        enc = self._room_encounter()
        if not enc:
            return {"success": True, "spell": "Wall of Fire",
                    "message": "A wall of roaring flame rises - but there is no one there to be caught in it."}
        dice = max(0, X)
        first = sum(random.randint(1, 6) for _ in range(dice)) + dice
        res = self._spell_damage_encounter(enc[0], first, source="A wall of fire crashes through the room. ")
        if not res.get("monster_dead"):
            self._apply_buff("Wall of Fire", 5, {"dot": dice, "dice": 6})
            res["message"] += " The flames linger, burning the creature each round."
        res["spell"] = "Wall of Fire"
        return res

    def _spell_disintegrate(self):
        enc = self._room_encounter()
        if not enc:
            return {"success": True, "spell": "Disintegrate",
                    "message": "The disintegrating ray flashes - but there is nothing there to unmake."}
        res = self._spell_damage_encounter(enc[0], 9999, no_corpse=True, source="A disintegrating ray lances out. ")
        res["spell"] = "Disintegrate"
        res["message"] += " What remains crumbles to gray dust - no corpse, no trace."
        return res

    def _spell_kill(self):
        enc = self._room_encounter()
        if not enc:
            return {"success": True, "spell": "Kill",
                    "message": "You speak the word of death - but there is no one there to hear it."}
        res = self._spell_damage_encounter(enc[0], 9999, source="A cold word of death leaves your lips. ")
        res["spell"] = "Kill"
        return res

    # --- spell control infrastructure (sub-phase 2d, batch 2) ---
    def _encounter_damage_multiplier(self, room_id, label):
        """A sleeping target takes 10x damage (per-actor)."""
        ctrl = self.monster_control.get(f"{room_id}|{label}", {})
        return 10 if ctrl.get("sleep", 0) > 0 else 1

    def _tick_monster_control(self, room_id, monster_name):
        """Decrement control durations on a turn; webbed targets struggle free.
        Returns a list of message lines (per-actor)."""
        ctrl = self.monster_control.get(f"{room_id}|{monster_name}")
        if not ctrl:
            return []
        lines = []
        if ctrl.get("sleep", 0) > 0:
            ctrl["sleep"] -= 1
            if ctrl["sleep"] <= 0:
                lines.append(f"The {monster_name} stirs and wakes.")
        if ctrl.get("hold", 0) > 0:
            ctrl["hold"] -= 1
            if ctrl["hold"] <= 0:
                lines.append(f"The {monster_name} breaks free of the hold.")
        if ctrl.get("confuse", 0) > 0:
            ctrl["confuse"] -= 1
            if ctrl["confuse"] <= 0:
                lines.append(f"The {monster_name}'s eyes focus - the confusion fades.")
        if ctrl.get("web", 0) > 0:
            roll = self.roll_dice(20, 0)
            if roll >= 15:
                ctrl["web"] = 0
                lines.append(f"The {monster_name} tears free of the web! (roll {roll})")
            else:
                lines.append(f"The {monster_name} strains against the web. (roll {roll})")
        if not any(ctrl.values()):
            self.monster_control.pop(f"{room_id}|{monster_name}", None)
        return lines

    def _tick_all_monster_controls(self):
        """Tick every active control state (movement turns), per-actor."""
        for key in list(self.monster_control.keys()):
            room_id, label = key.split("|", 1)
            room = self.world.rooms.get(room_id) or self.world.town_rooms.get(room_id)
            enc = self._normalize_encounter((room or {}).get("encounter"))
            if label not in enc:
                self.monster_control.pop(key, None)
                continue
            self._tick_monster_control(room_id, label)

    def _spell_sleep(self):
        enc = self._room_encounter()
        if not enc:
            return {"success": True, "spell": "Sleep",
                    "message": "The heavy mist settles - but there is nothing there to lull."}
        pos = self.player.data["position"]
        room_id = f"{pos['x']}_{pos['y']}_{pos['z']}"
        bestiary = self.world.generator.bestiary
        mdata = bestiary.get(enc, {})
        raw_hp = mdata.get("hp", 10)
        if raw_hp <= 20:
            self._monster_control_for(room_id, enc)["sleep"] = 3
            self.player.save()
            return {"success": True, "spell": "Sleep",
                    "message": f"A heavy mist descends and the {enc} sinks into a deep, untroubled sleep. "
                               f"(3 rounds; it takes 10x damage, and you may simply pass by it. "
                               f"Killing it in sleep costs -3 Virtue.)"}
        dc = 8 + 1 + self._int_bonus()
        roll = self.roll_dice(20, 0)
        if roll >= dc:
            return {"success": True, "spell": "Sleep",
                    "message": f"A heavy mist descends, but the {enc} shakes it off and stays awake. "
                               f"(save roll {roll} vs DC {dc})"}
        self._monster_control_for(room_id, enc)["sleep"] = 3
        self.player.save()
        return {"success": True, "spell": "Sleep",
                "message": f"The mist takes hold and the {enc} slips into sleep. (save roll {roll} vs DC {dc}; "
                           f"3 rounds, 10x damage, you may pass by it)"}

    def _spell_hold_person(self):
        enc = self._room_encounter()
        if not enc:
            return {"success": True, "spell": "Hold Person",
                    "message": "You speak the words of holding - but there is no one there to bind."}
        bestiary = self.world.generator.bestiary
        mdata = bestiary.get(enc, {})
        if mdata.get("kind") != "person":
            return {"success": True, "spell": "Hold Person",
                    "message": f"The words of holding wash over the {enc} and do nothing - it is no person."}
        pos = self.player.data["position"]
        room_id = f"{pos['x']}_{pos['y']}_{pos['z']}"
        self._monster_control_for(room_id, enc)["hold"] = 3
        self.player.save()
        return {"success": True, "spell": "Hold Person",
                "message": f"The {enc} is rooted in place - no block, no attack, no movement. (3 rounds)"}

    def _spell_web(self):
        enc = self._room_encounter()
        if not enc:
            return {"success": True, "spell": "Web",
                    "message": "The webbing sprays out - but there is nothing there to ensnare."}
        bestiary = self.world.generator.bestiary
        mdata = bestiary.get(enc, {})
        roll = self.roll_dice(20, self.get_stat_bonus("DEX"))
        if roll < mdata.get("ac", 10):
            return {"success": True, "spell": "Web",
                    "message": f"The webbing sprays toward the {enc} and misses. "
                               f"(roll {roll} vs AC {mdata.get('ac',10)})"}
        pos = self.player.data["position"]
        room_id = f"{pos['x']}_{pos['y']}_{pos['z']}"
        self._monster_control_for(room_id, enc)["web"] = 1
        self.player.save()
        return {"success": True, "spell": "Web",
                "message": f"Thick webbing erupts and ensnares the {enc}! (roll {roll}) It cannot attack or block "
                           f"until it tears free or is killed."}

    def _spell_charm_person(self):
        enc = self._room_encounter()
        if not enc:
            return {"success": True, "spell": "Charm Person",
                    "message": "You offer the words of charm - but there is no one there to hear them."}
        bestiary = self.world.generator.bestiary
        mdata = bestiary.get(enc, {})
        if mdata.get("kind") != "person":
            return {"success": True, "spell": "Charm Person",
                    "message": f"The words of charm find no purchase in the {enc} - it is no person."}
        pos = self.player.data["position"]
        room_id = f"{pos['x']}_{pos['y']}_{pos['z']}"
        self._clear_room_monster(room_id, enc)
        self._clear_monster_control(room_id, enc)
        self.player.save()
        return {"success": True, "spell": "Charm Person", "pacified": True,
                "message": f"Your words of charm take hold. The {enc} regards you with sudden warmth, "
                           f"then turns and departs, leaving you in peace."}

    def _spell_charm_monster(self):
        enc = self._room_encounter()
        if not enc:
            return {"success": True, "spell": "Charm Monster",
                    "message": "You offer the words of charm - but there is no one there to hear them."}
        pos = self.player.data["position"]
        room_id = f"{pos['x']}_{pos['y']}_{pos['z']}"
        self._clear_room_monster(room_id, enc)
        self._clear_monster_control(room_id, enc)
        self.player.save()
        return {"success": True, "spell": "Charm Monster", "pacified": True,
                "message": f"Your words of charm take hold. The {enc} regards you with sudden warmth, "
                           f"then turns and departs, leaving you in peace."}

    def _spell_confusion(self):
        enc = self._room_encounter()
        if not enc:
            return {"success": True, "spell": "Confusion",
                    "message": "The dissonant chord rings out - but there is nothing there to confuse."}
        pos = self.player.data["position"]
        room_id = f"{pos['x']}_{pos['y']}_{pos['z']}"
        self._monster_control_for(room_id, enc)["confuse"] = 3
        self.player.save()
        return {"success": True, "spell": "Confusion",
                "message": f"A dissonant chord of confusion washes over the {enc}. "
                           f"(3 rounds: -4 to its attacks, and 1 time in 6 it strikes itself)"}

    # --- spell utility infrastructure (sub-phase 2d, batch 3) ---
    def _monster_sees_invisible(self, monster_name):
        """True if the named monster can see through Invisibility."""
        if not monster_name:
            return False
        monster = self.world.generator.bestiary.get(monster_name, {})
        return "see_invisible" in set(monster.get("special", []))

    def _buff_active(self, *names):
        """True if any of the named buffs is currently active."""
        for b in self.player.data.get("buffs", []):
            if b.get("name") in names and b.get("turns", 0) > 0:
                return True
        return False

    def _opposites(self):
        return {"North": "South", "South": "North", "East": "West", "West": "East",
                "Up": "Down", "Down": "Up"}

    def _spell_lock(self):
        """Lock: sets the room's door(s) to locked state, persists."""
        pos = self.player.data["position"]
        rid = f"{pos['x']}_{pos['y']}_{pos['z']}"
        room = self.world.rooms.get(rid)
        if room is None:
            return {"success": True, "spell": "Lock",
                    "message": "You are in town - there is no door here to lock."}
        doors = room.get("doors", {})
        locked = []
        for d, door in doors.items():
            if door.get("state") != "locked":
                door["state"] = "locked"
                pair = self._edge_rooms(rid, d)
                if pair:
                    far, back = pair[1], pair[2]
                    if back in far.get("doors", {}):
                        far["doors"][back]["state"] = "locked"
                locked.append(d)
        if not locked:
            return {"success": True, "spell": "Lock",
                    "message": "The doors here are already locked."}
        self.world.save()
        dirs = ", ".join(sorted(locked))
        return {"success": True, "spell": "Lock",
                "message": f"Runes of binding flash and the door(s) to the {dirs} click shut, locked tight."}

    def _spell_knock(self):
        """Knock: UNLOCKS every locked door in the room (state -> closed/openable)."""
        pos = self.player.data["position"]
        rid = f"{pos['x']}_{pos['y']}_{pos['z']}"
        room = self.world.rooms.get(rid)
        if room is None:
            return {"success": True, "spell": "Knock",
                    "message": "You are in town - there is no door here to knock."}
        doors = room.get("doors", {})
        unlocked = []
        for d, door in doors.items():
            if door.get("state") == "locked":
                door["state"] = "closed"
                pair = self._edge_rooms(rid, d)
                if pair:
                    far, back = pair[1], pair[2]
                    if back in far.get("doors", {}):
                        far["doors"][back]["state"] = "closed"
                unlocked.append(d)
        if not unlocked:
            return {"success": True, "spell": "Knock",
                    "message": "A resonant knock echoes out - but no lock here answers."}
        self.world.save()
        dirs = ", ".join(sorted(unlocked))
        return {"success": True, "spell": "Knock",
                "message": f"A deep, resonant knock ripples through the stone - the lock(s) to the {dirs} open with a click."}

    def _spell_levitate(self):
        """Levitate: 5-turn buff; pits and water don't hurt you (forward: gap bypass)."""
        self._apply_buff("Levitate", 5, {"move_flags": {"pit": True, "water": True, "gap": True}})
        return {"success": True, "spell": "Levitate",
                "message": "You rise gently off the ground and hover. (5 turns: pits and water won't catch you)"}

    def _spell_fly(self):
        """Fly: 10-turn buff; pits/water immune (forward: gap bypass)."""
        self._apply_buff("Fly", 10, {"move_flags": {"pit": True, "water": True, "gap": True}})
        return {"success": True, "spell": "Fly",
                "message": "Invisible wings catch the air and you soar. (10 turns: pits and water are no obstacle)"}

    def _spell_invisibility(self):
        """Invisibility: 5-turn buff; no ambushes; existing encounter no longer blocks you."""
        self._apply_buff("Invisibility", 5, {"stealth": True})
        return {"success": True, "spell": "Invisibility",
                "message": "The light bends around you and you vanish. (5 turns: no ambushes, and a monster that was blocking you can no longer hold you)"}

    def _spell_water_breathing(self):
        """Water Breathing: 10-turn buff; underwater movement (forward: water rooms/levels)."""
        self._apply_buff("Water Breathing", 10, {"move_flags": {"underwater": True}})
        return {"success": True, "spell": "Water Breathing",
                "message": "You draw breath from the water itself. (10 turns: you can move through water)"}

    def _spell_passwall(self):
        """Passwall: just removes an obstacle in the room (permanent)."""
        pos = self.player.data["position"]
        rid = f"{pos['x']}_{pos['y']}_{pos['z']}"
        room = self.world.rooms.get(rid)
        if room is None:
            return {"success": True, "spell": "Passwall",
                    "message": "You are in town - there is no wall here to pass through."}
        direction, ob = self._find_obstacle_way(room)
        if ob is None:
            return {"success": True, "spell": "Passwall",
                    "message": "The spell passes through the air - but there is no wall here to dissolve."}
        name = ob.get("name", "obstacle")
        pair = self._edge_rooms(rid, direction)
        if pair is None:
            room["obstacles"].pop(direction, None)
        else:
            self._promote_edge(pair, direction, rid=rid)
        self.world.save()
        return {"success": True, "spell": "Passwall",
                "message": f"The {name} shimmers, thins, and is simply gone. The way to the {direction} stands open."}

    def _spell_teleport(self):
        """Teleport: the reagents are mixed; now the player names where they
        know where they're going. Returns needs_coords so the game layer can
        prompt, then call _teleport_apply((x, y, z))."""
        return {"success": True, "spell": "Teleport", "needs_coords": True,
                "message": "The mixture hums, ready to fold space. Where do you know where you're going? (x, y, z)"}

    def _teleport_apply(self, coord):
        """Apply a Teleport to (x, y, z) - reagents already consumed.
        Guaranteed to land there; out-of-bounds (The Void) = comedic death."""
        if not coord:
            return {"success": False, "spell": "Teleport",
                    "message": "You lose your nerve and the mixture fizzles out. (The reagents are spent.)"}
        x, y, z = coord
        target_id = f"{x}_{y}_{z}"
        known = (target_id in self.world.town_rooms or target_id in self.world.rooms)
        if known:
            self.player.data["position"] = {"x": x, "y": y, "z": z}
            self.player.save()
            room = self.world.get_room(x, y, z)
            self.player.update_satiety(-1)
            return {"success": True, "spell": "Teleport",
                    "message": f"You fold space and step out in {room.get('name', 'a room')} ({x}, {y}, {z}).",
                    "position": {"x": x, "y": y, "z": z}, "room": room}
        # Out-of-bounds: The Void or elsewhere. Comedic death.
        self.player.die()
        self.death_message = (
            f"You fold space and materialize at ({x}, {y}, {z}) - which is The Void, "
            "a place that is emphatically not a place. You burst out of the middle of a "
            "solid stone wall, half in and half out, your robes caught on a protruding "
            "iron bar. A passing priest stops mid-sermon, points at you, and faints. "
            "You have, in a word, died. (Type 'reset' to return to the Temple of Radiance.)")
        return {"success": True, "spell": "Teleport", "void_death": True,
                "message": self.death_message}

    def _spell_locate(self):
        """Locate Object or Person: the reagents are mixed; now the player
        names what to find. Returns needs_target so the game layer can prompt,
        then call _locate_apply(target)."""
        return {"success": True, "spell": "Locate Object or Person", "needs_target": True,
                "message": "The mixture shivers, straining toward something. What do you want to find? (an item or a person)"}

    def _locate_apply(self, target):
        """Apply a Locate to a named item or NPC - reagents already consumed.
        BFS the current floor, report direction + rooms away."""
        if not target:
            return {"success": False, "spell": "Locate Object or Person",
                    "message": "You lose your nerve and the mixture fizzles out. (The reagents are spent.)"}
        target = target.strip()
        pos = self.player.data["position"]
        z = pos["z"]
        # NPC?
        if target in NPC_ROOM_BY_NAME:
            npc_rid = NPC_ROOM_BY_NAME[target]
            nx, ny, nz = map(int, npc_rid.split("_"))
            if nz != z:
                return {"success": True, "spell": "Locate Object or Person",
                        "message": f"{target} is not on this floor."}
            return self._locate_report(pos, npc_rid, f"{target}")
        # Item: BFS the floor's rooms for a drop match.
        start = f"{pos['x']}_{pos['y']}_{pos['z']}"
        self.world.get_room(pos["x"], pos["y"], pos["z"])
        rooms = self.world.rooms
        from collections import deque
        seen = {start: (None, 0)}
        dq = deque([start])
        found = None
        while dq:
            cur = dq.popleft()
            room = rooms.get(cur)
            if not room:
                continue
            for dr in room.get("dropped_items") or []:
                if dr.get("name", "").lower() == target.lower():
                    found = cur
                    break
            if found:
                break
            for d, to in room.get("exits", {}).items():
                if to not in seen:
                    seen[to] = (d, seen[cur][1] + 1)
                    dq.append(to)
        if found:
            return self._locate_report(pos, found, f"the {target}")
        return {"success": True, "spell": "Locate Object or Person",
                "message": f"You sense nothing like the {target} on this floor."}

    def _locate_report(self, pos, target_rid, label):
        tx, ty, tz = map(int, target_rid.split("_"))
        if (tx, ty, tz) == (pos["x"], pos["y"], pos["z"]):
            return {"success": True, "spell": "Locate Object or Person",
                    "message": f"{label} is right here with you."}
        # direction from current room
        room = self.world.get_room(pos["x"], pos["y"], pos["z"])
        exits = room.get("exits", {})
        dirs = []
        for d, to in exits.items():
            if to == target_rid:
                dirs.append(d)
        dist = None
        # BFS distance (reuse simple search)
        from collections import deque
        start = f"{pos['x']}_{pos['y']}_{pos['z']}"
        seen = {start: 0}
        dq = deque([start])
        while dq:
            cur = dq.popleft()
            for d, to in self.world.rooms.get(cur, {}).get("exits", {}).items():
                if to not in seen:
                    seen[to] = seen[cur] + 1
                    dq.append(to)
                if to == target_rid:
                    dist = seen[to]
                    break
            if dist is not None:
                break
        if dirs:
            return {"success": True, "spell": "Locate Object or Person",
                    "message": f"{label} is to the {', '.join(sorted(dirs))} - about {dist} room(s) away."}
        return {"success": True, "spell": "Locate Object or Person",
                "message": f"{label} is on this floor, {dist} room(s) away, but not in a direct line."}

    def _spell_continuous_light(self):
        """Continuous Light: 100-turn light (bigger than Light's 50)."""
        self.player.data["light_duration"] = max(self.player.data.get("light_duration", 0), 100)
        self.player.save()
        return {"success": True, "spell": "Continuous Light",
                "message": "A steady, unwavering glow settles around you, ignoring torch-fuel and lantern-oil alike. (100 turns)"}

    def _spell_remove_curse(self):
        """Remove Curse: clears Cursed status + curse debuffs (restores max HP)."""
        effs = self.player.data.get("status_effects", [])
        had = [e for e in effs if e in ("Cursed", "Curse", "Diseased")]
        if not had:
            return {"success": True, "spell": "Remove Curse",
                    "message": "You wash away the taint of curses - but you were not cursed."}
        self.player.data["status_effects"] = [e for e in effs if e not in ("Cursed", "Curse")]
        # If a curse had capped max HP (disease-style), restore derived stats.
        if self.player.data.get("max_hp", 0) < (10 + ((self.player.data["stats"]["CON"] - 10) * 2)):
            self.player.refresh_derived_stats()
        self.player.save()
        cleared = ", ".join(sorted(set(had)))
        return {"success": True, "spell": "Remove Curse",
                "message": f"A cold shudder runs off you as the curse breaks. ({cleared} removed)"}

    def _spell_dispel_magic(self):
        """Dispel Magic: removes your active buffs + the room's magical state
        (monster control: web/hold/sleep/confuse, wall of fire)."""
        p = self.player.data
        buffs_before = [b.get("name") for b in p.get("buffs", [])]
        p["buffs"] = []
        # clear room's magical monster-control state
        rid = f"{p['position']['x']}_{p['position']['y']}_{p['position']['z']}"
        ctrl = self.monster_control.get(rid, {})
        had_ctrl = {k: v for k, v in ctrl.items() if v > 0}
        self._clear_monster_control(rid)
        # clear continuous light (spell light)
        p["light_duration"] = 0
        self.player.save()
        self.world.save()
        parts = []
        if buffs_before:
            parts.append("your buffs (" + ", ".join(sorted(set(buffs_before))) + ")")
        if had_ctrl:
            parts.append("the room's enchantments (" + ", ".join(sorted(had_ctrl)) + ")")
        if not parts:
            return {"success": True, "spell": "Dispel Magic",
                    "message": "You wave away the magic in the air - but there was nothing to dispel."}
        return {"success": True, "spell": "Dispel Magic",
                "message": "A pulse of dispelling energy washes over you and the room, unravelling " + " and ".join(parts) + "."}

    def _spell_find_traps(self):
        """Find Traps: reports the trap in this room + which adjacent rooms have them."""
        pos = self.player.data["position"]
        rid = f"{pos['x']}_{pos['y']}_{pos['z']}"
        room = self.world.get_room(pos["x"], pos["y"], pos["z"])
        here = bool(room.get("trap"))
        here_type = room.get("trap_type", "a trap")
        adj = []
        for d, to in room.get("exits", {}).items():
            ar = self.world.get_room(*map(int, to.split("_")))
            if ar.get("trap"):
                adj.append(f"to the {d} ({ar.get('trap_type', 'a trap')})")
        lines = []
        if here:
            lines.append(f"This room holds {here_type}.")
        else:
            lines.append("No trap here.")
        if adj:
            lines.append("You sense traps " + "; ".join(adj) + ".")
        else:
            lines.append("The adjoining passages feel clean.")
        return {"success": True, "spell": "Find Traps",
                "message": " " + " ".join(lines)}

    # --- spell: batch 4 (LLM tools) ---
    def _room_id(self):
        pos = self.player.data["position"]
        return f"{pos['x']}_{pos['y']}_{pos['z']}"

    def _polymorph_active(self, player=None):
        """Return the active Polymorph buff's creature tag, or None."""
        p = player if player is not None else self.player
        for b in p.data.get("buffs", []):
            if b.get("name") == "Polymorph" and b.get("turns", 0) > 0:
                return b.get("tags", {}).get("polymorph")
        return None

    def _spell_polymorph(self):
        """Polymorph: reagents mixed; the player names a creature (with caution
        and precision, or it fizzles). Returns needs_target so the game layer
        can prompt, then call _polymorph_apply(creature)."""
        return {"success": True, "spell": "Polymorph", "needs_target": True,
                "message": "The mixture shivers, ready to fold your form. Choose your creature with caution and precision - name one that is in the bestiary, or it will fizzle. What do you wish to become?"}

    def _polymorph_apply(self, creature):
        """Apply a Polymorph to a named creature - reagents already consumed.
        If it is in the bestiary, take its AC + attack bonus for 3 turns;
        otherwise the reagents fizzle (you are unchanged)."""
        if not creature:
            return {"success": False, "spell": "Polymorph",
                    "message": "You lose your nerve and the mixture fizzles out. (The reagents are spent.)"}
        key = self._find_monster(creature.strip())
        if not key:
            return {"success": True, "spell": "Polymorph", "fizzled": True,
                    "message": f"The mixture writhes and snaps back to itself - no such creature is in the bestiary. The reagents fizzle. (You are unchanged.)"}
        b = self.world.generator.bestiary.get(key, {})
        self._apply_buff("Polymorph", 3, {"polymorph": {"ac": b.get("ac", 10),
                                                        "atk": b.get("atk", 0),
                                                        "name": key}})
        return {"success": True, "spell": "Polymorph", "creature": key,
                "ac": b.get("ac", 10), "atk": b.get("atk", 0),
                "message": f"Your flesh folds and reshapes into a {key}. (3 turns: AC {b.get('ac', 10)}, attack bonus {b.get('atk', 0)}.)"}

    def _room_objects(self, room):
        """The unanimate objects actually in the room (the telekinesis guardrail)."""
        objs = []
        if not room:
            return objs
        for dr in room.get("dropped_items") or []:
            n = dr.get("name")
            if n:
                objs.append(n)
        if (room.get("loot") or room.get("chest")) and not room.get("loot_taken"):
            cont = room.get("container") or (room.get("chest") or {}).get("item") or "chest"
            objs.append(cont)
        return objs

    def _spell_telekinesis(self):
        """Telekinesis: reagents mixed; the player describes the action
        (throw an object at the encounter, or pull an object to you). Returns
        needs_target + the room's object list (the guardrail)."""
        room = self._authoritative_room(self._room_id())
        objs = self._room_objects(room)
        return {"success": True, "spell": "Telekinesis", "needs_target": True,
                "objects": objs,
                "message": "The air trembles at your command. What do you do? (throw <object> / pull <object>)"}

    def _telekinesis_apply(self, action):
        """Apply a Telekinesis action - reagents already consumed.
        'throw <object>': Xd4+X to the encounter. 'pull <object>': bring it close."""
        if not action:
            return {"success": False, "spell": "Telekinesis",
                    "message": "You lose your nerve and the air stills. (The reagents are spent.)"}
        action = action.strip()
        parts = action.split(None, 1)
        verb = parts[0].lower()
        target = parts[1].strip() if len(parts) > 1 else ""
        room = self._authoritative_room(self._room_id())
        objs = self._room_objects(room)
        match, ambig = resolve_item_name(target, objs)
        if not match:
            if ambig:
                return {"success": True, "spell": "Telekinesis",
                        "message": f"Which {target}? I sense: {', '.join(ambig)}."}
            return {"success": True, "spell": "Telekinesis",
                    "message": f"There is no {target or 'object'} here to move. (The air stills.)"}
        if verb in ("throw", "hurl", "fling", "launch", "toss"):
            X = self._int_bonus()
            enc = self._room_encounter()
            if not enc:
                return {"success": True, "spell": "Telekinesis",
                        "message": f"You hurl the {match} - but there is nothing there to strike."}
            dice = max(0, X)
            dmg = sum(random.randint(1, 4) for _ in range(dice)) + dice
            res = self._spell_damage_encounter(enc, dmg,
                        source=f"A telekinetic blast hurls the {match} into the {enc}. ")
            res["spell"] = "Telekinesis"
            res["object"] = match
            return res
        if verb in ("pull", "draw", "fetch", "bring", "grab", "snatch"):
            cont_name = room.get("container") or (room.get("chest") or {}).get("item") or "chest"
            if (room.get("loot") or room.get("chest")) and not room.get("loot_taken") \
                    and match.lower() == cont_name.lower():
                return {"success": True, "spell": "Telekinesis",
                        "message": f"The {match} slides across the floor to your feet. (Try 'open {match.lower()}'.)"}
            for dr in room.get("dropped_items") or []:
                if dr.get("name") == match:
                    room["dropped_items"].remove(dr)
                    break
            self.world.save()
            res = self.player.add_item(match, 1)
            if res.get("success"):
                return {"success": True, "spell": "Telekinesis",
                        "message": f"The {match} drifts across the floor into your pack."}
            return {"success": True, "spell": "Telekinesis",
                    "message": f"The {match} drifts to you - but you cannot carry it. ({res.get('message', '')})"}
        return {"success": True, "spell": "Telekinesis",
                "message": "Throw or pull? (throw <object> / pull <object>)"}

    def _spell_speak_with_plants(self):
        """Speak with Plants: 5-turn buff; plant obstacles (Wall of Thorns)
        yield a page, a lore line, or a secret."""
        self._apply_buff("Speak with Plants", 5, {"speak": "plants"})
        return {"success": True, "spell": "Speak with Plants",
                "message": "The thorns lean in and whisper. (5 turns: the plants here will speak to you - ask them.)"}

    def _speak_with_plants_reveal(self):
        """Pick a validated readable from the ledger for Speak with Plants to
        reveal. Returns {'name','kind','text'} or None if the ledger is empty."""
        if not READABLE_DATA:
            return None
        key = random.choice(list(READABLE_DATA.keys()))
        v = READABLE_DATA.get(key, {})
        return {"name": key, "kind": v.get("kind", "page"), "text": v.get("text", "")}

    def _spell_speak_with_animals(self):
        """Speak with Animals: 5-turn buff; animal (beast) encounters can be
        asked questions. Returns the animal sheet (the validated identity the
        LLM roleplays) + room context. If no beast here, just the buff."""
        self._apply_buff("Speak with Animals", 5, {"speak": "animals"})
        room = self._authoritative_room(self._room_id())
        monsters = self._normalize_encounter((room or {}).get("encounter"))
        beast = None
        for monster in monsters:
            b = self.world.generator.bestiary.get(self._find_monster(monster), {})
            if b.get("kind") == "beast":
                beast = (monster, b)
                break
        if not beast:
            return {"success": True, "spell": "Speak with Animals", "no_animal": True,
                    "message": "The words settle - but there is no animal here to speak with. (5 turns: the animals here will answer you, if any.)"}
        monster, b = beast
        ctx = self.get_scene_context()
        return {"success": True, "spell": "Speak with Animals", "needs_target": True,
                "animal": monster, "kind": b.get("kind", "unknown"),
                "special": b.get("special", []),
                "room_name": ctx.get("room_name"), "room_contents": ctx.get("room_contents"),
                "message": f"The {monster} turns its gaze on you, and its growls resolve into words. (5 turns: ask it what you will.)"}

    def _spell_commune(self):
        """Commune with the Dead: reagents mixed; a corpse in the room answers
        up to 3 yes/no questions. Returns needs_target + the corpse sheet
        (the validated identity the LLM roleplays) + room context for truthful
        answers. If no corpse, the reagents fizzle."""
        room = self._authoritative_room(self._room_id())
        corpses = [c for c in self._clear_all_corpses(self._room_id()) if c]
        if not corpses:
            return {"success": True, "spell": "Commune with the Dead",
                    "message": "The incense curls - but there is no corpse here to commune with. (The reagents are spent.)"}
        # Several corpses: let the unsearched one speak first.
        unsearched = [c for c in corpses if not c.get("searched")]
        corpse = (unsearched or corpses)[0]
        monster = corpse.get("monster", "it")
        b = self.world.generator.bestiary.get(monster, {})
        ctx = self.get_scene_context()
        return {"success": True, "spell": "Commune with the Dead", "needs_target": True,
                "corpse": monster, "kind": b.get("kind", "unknown"),
                "special": b.get("special", []),
                "room_name": ctx.get("room_name"), "room_contents": ctx.get("room_contents"),
                "message": f"The corpse of the {monster} stirs. It will answer three questions - yes or no. Ask wisely."}

    def _spell_wish(self):
        """Wish: wildcard - the player describes the effect; the LLM gets all
        tools and executes the plan as closely as possible. Returns needs_wish
        so the game layer can prompt, then run the improvise loop."""
        return {"success": True, "spell": "Wish", "needs_wish": True,
                "message": "The mixture blazes with potential. What do you wish for?"}

    def meditate(self, shrine_type, stat_name):
        if stat_name not in self.player.data["stats"]:
            return {"success": False, "message": "Invalid stat."}
        virtue = self.player.data["virtue"]
        if shrine_type == "town" and virtue >= 50:
            self.player.data["virtue"] -= 50
            self.player.data["stats"][stat_name] += 1
            self.player.save()
            return {"success": True, "message": f"{stat_name} increased!"}
        elif shrine_type == "forgiveness" and virtue < 0:
            self.player.data["virtue"] = 0
            self.player.save()
            return {"success": True, "message": "Sins washed away."}
        elif shrine_type == "anti_virtue" and virtue <= -50:
            self.player.data["virtue"] += 50
            self.player.data["stats"][stat_name] += 1
            self.player.save()
            return {"success": True, "message": f"Darkness empowers {stat_name}!"}
        elif shrine_type == "negative_shrine":
            if virtue > 0:
                if "Cursed" not in self.player.data.get("status_effects", []):
                    self.player.data.setdefault("status_effects", []).append("Cursed")
                self.player.save()
                return {"success": True, "message": "The darkness recoils from your light - you are CURSED (-1 to hit)."}
            elif virtue <= -50:
                self.player.data["virtue"] += 50
                self.player.data["stats"][stat_name] += 1
                self.player.save()
                return {"success": True, "message": f"The darkness drinks your guilt. {stat_name} increases! (Virtue +50)"}
            else:
                return {"success": True, "message": "The black altar is silent. Your soul is neither light nor dark enough to stir it."}
        return {"success": False, "message": "Meditation failed."}

    # --- The inn's upper guest room (Inn Key / free sleep / auto re-lock) ---
    def _inn_door(self):
        # Live dict of the inn's locked stairway door, or None.
        room = self.world.town_rooms.get(INN_BASE_ID)
        if room is None:
            return None
        return room.get("doors", {}).get(INN_KEY_ITEM)

    def _inn_tick(self):
        # Per-tick inn upkeep (runs as the satiety clock advances):
        #   (a) an open stairway re-locks on its own after INN_LOCK_TICKS ticks;
        #   (b) unused Inn Keys in the pack expire after INN_LOCK_TICKS ticks
        #       (anti-stockpile: a key is only "fresh" for a 50-tick window, so
        #       pre-purchasing a pile of keys is not an exploit).
        p = self.player.data
        if p.get("inn_key"):
            door = self._inn_door()
            if door is not None and door.get("state") == "closed" \
                    and p["satiety"] <= p["inn_key"] - INN_LOCK_TICKS:
                door["state"] = "locked"
                p["inn_key"] = None
                p["inn_slept"] = False
                self.player.save()
                self._save_room(INN_BASE_ID)
        ages = p.get("inn_key_ages")
        if ages and p["inventory"].get(INN_KEY_ITEM, 0) > 0:
            keep = [a for a in ages if p["satiety"] > a - INN_LOCK_TICKS]
            expired = len(ages) - len(keep)
            if expired > 0:
                p["inventory"][INN_KEY_ITEM] = max(0, p["inventory"][INN_KEY_ITEM] - expired)
                p["inn_key_ages"] = keep
                self.player.save()

    def sleep(self):
        pos = self.player.data["position"]
        z = pos["z"]
        satiety_cost = 15
        # The inn's upper guest room: a full, restful sleep. The Inn Key (3 gp)
        # was the whole transaction, so nothing is charged here.
        if pos["x"] == -1 and pos["y"] == -1 and z == 1:
            if self.player.data["satiety"] < satiety_cost:
                return {"success": False, "message": "You are too hungry to sleep properly; you need more food."}
            self.player.data["satiety"] = max(0, self.player.data["satiety"] - satiety_cost)
            self.player.update_hp(self.player.data["max_hp"] - self.player.data["hp"])
            self._bump_virtue_day()
            self.sleep_until_morning()
            self.player.data["inn_slept"] = True
            self.player.save()
            label = self.time_label()
            return {"success": True, "message": "You sleep deeply in the inn's guest room and wake fully refreshed."
                    + (f" It is {label}." if label else "")}
        if z == 0:
            if pos["x"] == -1 and pos["y"] == -1:
                return {"success": False, "message": "The common room is no place to sleep. Go up the locked stairway to the guest room - the barkeep keeps the keys ('shop')."}
        else:
            if "Bedroll" not in self.player.data["inventory"]:
                return {"success": False, "message": "You don't have a bedroll. Sleeping on the cold dungeon floor is a recipe for disaster"}
            if self.player.data["satiety"] < satiety_cost:
                return {"success": False, "message": "You are too hungry to sleep properly; you need more food."}
            if z < 0 and not self._buff_active("Invisibility"):
                ambush_chance = 0.1 * abs(z)
                if random.random() < ambush_chance:
                    monster = self.world.generator._stock_monster(z)
                    attack = self.resolve_monster_attack(monster)
                    self.player.update_hp(-attack["damage"])
                    self.player.data["light_fuel"]["torch"] = 0
                    self.advance_phases(1)
                    return {"success": False,
                            "message": f"You were ambushed in your sleep! A {monster} strikes you for {attack['damage']} damage before you can even react!",
                            "ambush": True, "monster": monster}
            self.player.data["light_fuel"]["torch"] = 0
            self.player.data["satiety"] = max(0, self.player.data["satiety"] - satiety_cost)
            lost_hp = self.player.data["max_hp"] - self.player.data["hp"]
            self.player.update_hp(lost_hp // 2)
            self._bump_virtue_day()
            self.advance_phases(2)
            return {"success": True, "message": "You curl up in your bedroll for a few hours of uneasy sleep. You feel somewhat rested."}

    def _pick_monster_target(self):
        """C2: a monster action hits ONE party member - the one with the lowest
        current HP (ties broken by party order: Caller first, then satellites).
        In a solo run this is just the Caller, so behaviour is unchanged."""
        members = [self.player] + [s for s in self.satellites if not s.is_down()]
        if not members:
            return self.player
        return min(members, key=lambda m: m.data["hp"])

    def resolve_combat_turn(self, monster_name, player_action, player_weapon, monster_action, formula=None, satellite_actions=None):
        room_id = self._room_id()
        room = self.world.get_room(self.player.data["position"]["x"],
                                   self.player.data["position"]["y"],
                                   self.player.data["position"]["z"])
        is_dark = (not self.room_is_lit(room)) and not self.player.is_lit()
        attack_penalty = -4 if is_dark else 0
        monster_id = self._find_monster(monster_name)
        bestiary = self.world.generator.bestiary
        if not monster_id:
            return {"error": "Monster not found."}
        ckey = f"{room_id}|{monster_name}"
        if ckey not in self.combat_state:
            self.combat_state[ckey] = bestiary[monster_id].get("hp", 10)
        m_hp = self.combat_state[ckey]
        p_hp = self.player.data["hp"]
        ctrl = self.monster_control.get(ckey, {})
        dmg_mult = self._encounter_damage_multiplier(room_id, monster_name)
        p_res = {"hit": False, "damage": 0, "message": ""}
        # Wall of Fire damage-over-time: burns the target each combat round
        wof = self._get_buff("Wall of Fire")
        if wof:
            w_dice = wof.get("tags", {}).get("dot", 0)
            if w_dice > 0:
                w_dmg = (sum(random.randint(1, 6) for _ in range(w_dice)) + w_dice) * dmg_mult
                self.combat_state[ckey] -= w_dmg
                p_res["message"] = (p_res.get("message") or "") + f" The wall of fire burns the {monster_name} for {w_dmg}."
            wof["turns"] -= 1
            if wof["turns"] <= 0:
                self.player.data["buffs"] = [b for b in self.player.data["buffs"] if b.get("name") != "Wall of Fire"]
                self.player.save()
        if player_action == "fight":
            p_res = self.resolve_attack(player_weapon, bestiary[monster_id]["ac"], penalty=attack_penalty)
            if p_res["hit"]:
                dealt = p_res["damage"] * dmg_mult
                self.combat_state[ckey] -= dealt
                p_res["damage"] = dealt
        elif player_action == "mix" and formula:
            spell_res = self.mix_reagents(formula)
            p_res["message"] = spell_res.get("message", "The mixture fizzles.")
            p_res["spell"] = spell_res.get("spell")
            p_res["auras"] = spell_res.get("auras")
        # ---- satellites act (C6: v1 verbs are 'fight' and 'pass') ----
        sat_results = []
        sat_actions = satellite_actions or {}
        for sat in self.satellites:
            act = sat_actions.get(sat.slot, "pass")
            if act == "fight" and not sat.is_down():
                s_res = self.resolve_attack(
                    sat.data["equipped"].get("weapon"), bestiary[monster_id]["ac"],
                    penalty=attack_penalty, player=sat)
                if s_res["hit"]:
                    dealt = s_res["damage"] * dmg_mult
                    self.combat_state[ckey] -= dealt
                    s_res["damage"] = dealt
                sat_results.append({"slot": sat.slot, "hit": s_res["hit"],
                                    "damage": s_res["damage"], "roll": s_res.get("roll")})
            else:
                sat_results.append({"slot": sat.slot, "hit": False, "damage": 0, "passed": True})
        poison_tick = self._tick_poison()
        m_res = {"hit": False, "damage": 0, "message": ""}
        is_sleeping = ctrl.get("sleep", 0) > 0
        is_held = ctrl.get("hold", 0) > 0
        is_webbed = ctrl.get("web", 0) > 0
        is_confused = ctrl.get("confuse", 0) > 0
        if is_sleeping:
            m_res["message"] = f"The {monster_name} slumbers, oblivious."
        elif is_held:
            m_res["message"] = f"The {monster_name} is rooted in place, unable to act."
        elif is_webbed:
            m_res["message"] = f"The {monster_name} strains against the webbing, unable to act."
        elif is_confused:
            if random.randint(1, 6) == 1:
                self_dmg = random.randint(1, 6) * dmg_mult
                self.combat_state[ckey] -= self_dmg
                m_res["message"] = f"The confused {monster_name} lashes out and strikes ITSELF for {self_dmg}!"
            else:
                m_res = self.resolve_monster_attack(monster_name, penalty=-4,
                                                    player=self._pick_monster_target())
                if m_res["hit"]:
                    m_res["target"].update_hp(-m_res["damage"])
                m_res["message"] = (m_res.get("message") or "") + " (confused: -4 to hit)"
        elif monster_action == "fight":
            m_res = self.resolve_monster_attack(monster_name, player=self._pick_monster_target())
            if m_res["hit"]:
                m_res["target"].update_hp(-m_res["damage"])
        elif monster_action == "special":
            special_msg = self.apply_monster_special(monster_name)
            m_res["message"] = special_msg or "The monster bellows menacingly!"
        elif monster_action == "flee":
            m_res["message"] = "The monster turns and flees into the darkness!"
            self._clear_room_monster(room_id, monster_name)
            self._clear_monster_control(room_id, monster_name)
            self.combat_state.pop(ckey, None)
        monster_dead = self.combat_state.get(ckey, 0) <= 0
        player_dead = self.player.data["hp"] <= 0
        sleep_kill = (monster_dead and is_sleeping and player_action in ("fight", "mix"))
        virtue_msg = None
        drop_lines = None
        if monster_dead:
            self._clear_room_monster(room_id, monster_name)
            self._clear_monster_control(room_id, monster_name)
            self.combat_state.pop(ckey, None)
            self.player.update_virtue(2)
            # The spoils are stowed inside the corpse - nothing is granted
            # until the player searches it.
            drop_lines = [self._deposit_corpse(
                room_id, monster_name, self.roll_monster_drops(monster_name))]
            if sleep_kill:
                # Striking a sleeping foe: the act itself costs an additional -3 Virtue
                # on top of the normal +2 kill reward (net -1).
                self.player.update_virtue(-3)
                virtue_msg = " You struck a sleeping foe - the act itself costs -3 Virtue."
            if player_action == "fight" and p_hp <= self.player.data["max_hp"] // 2:
                led = self._virtue_ledger()
                led["last_stand"] = led.get("last_stand", 0) + 1
                self.player.save()
                virtue_msg = (virtue_msg or "") + " Last stand logged - Raika will hear of it."
        control_msgs = self._tick_monster_control(room_id, monster_name)
        sat_down = [s.slot for s in self.satellites if s.is_down()]
        return {"player_attack": p_res, "monster_attack": m_res, "poison_tick": poison_tick,
                "satellite_attacks": sat_results, "sat_down": sat_down,
                "monster_hp": self.combat_state.get(ckey, 0), "monster_dead": monster_dead,
                "player_dead": player_dead, "virtue_msg": virtue_msg,
                "drop_lines": drop_lines, "control_msgs": control_msgs}

    # --- tools that lived on RpgModule, now engine methods ---
    def resolve_parley(self, monster_name, approach="negotiate", value=0):
        """A non-violent combat action: try to talk/bribe/hide your way out.

        The ENGINE rolls the die and decides the outcome so state stays
        authoritative; the caller (luminaria) feeds the verdict to the LLM for
        prose only. `approach` is a free-ish word (negotiate / intimidate /
        charm / bribe / offer / hide ...); `value` is gold thrown in as a bribe.

        Returns a verdict dict:
          pacified  -> the monster stands down and leaves (no kill, no drops)
          otherwise -> the parley fails and the monster takes a free swing
        """
        pos = self.player.data["position"]
        room_id = f"{pos['x']}_{pos['y']}_{pos['z']}"
        monster_id = self._find_monster(monster_name)
        bestiary = self.world.generator.bestiary
        if not monster_id:
            return {"success": False, "error": "Monster not found."}
        m = bestiary[monster_id]
        specials = m.get("special", [])
        intelligent = "intelligent" in specials

        approach = (approach or "negotiate").strip().lower()
        HIDE_WORDS = {"hide", "hiding", "sneak", "sneaking", "slip", "lurk",
                      "dodge", "evade", "skulk"}
        stat = "DEX" if approach in HIDE_WORDS else "CHA"
        bonus = self.get_stat_bonus(stat)
        # Mindless beasts, vermin and undead barely understand words.
        if not intelligent:
            bonus -= 4
        # A bribe/offer sweetens the deal: +1 per 25 gold, capped at +5.
        try:
            value = max(0, int(value or 0))
        except (TypeError, ValueError):
            value = 0
        gold_affordable = min(value, self.player.data.get("gold", 0))
        value_bonus = min(5, gold_affordable // 25)
        bonus += value_bonus
        dc = 12 if intelligent else 15

        roll = self.roll_dice(20, bonus)
        natural = roll - bonus  # the raw d20 before modifiers
        if natural == 1:
            pacified = False
        elif natural == 20:
            pacified = True
        else:
            pacified = roll >= dc

        # The offered coin is spent whether or not it works - it is thrown to the
        # creature the moment you make the offer.
        gold_spent = 0
        if gold_affordable > 0:
            self.player.update_gold(-gold_affordable)
            gold_spent = gold_affordable

        m_res = {"hit": False, "damage": 0, "message": ""}
        player_dead = False
        poison_tick = None
        if pacified:
            self._clear_room_monster(room_id, monster_name)
            self._clear_monster_control(room_id, monster_name)
            self.combat_state.pop(f"{room_id}|{monster_name}", None)
            self.player.update_virtue(1)  # a life spared, bloodless
        else:
            # The parley fails - the monster takes a free swing this round.
            poison_tick = self._tick_poison()
            m_res = self.resolve_monster_attack(monster_name)
            if m_res.get("hit"):
                self.player.update_hp(-m_res.get("damage", 0))
            player_dead = self.player.data["hp"] <= 0

        result = {"success": True, "pacified": pacified, "monster": monster_name,
                  "approach": approach, "stat": stat, "roll": roll, "natural": natural,
                  "dc": dc, "modifier": bonus, "intelligent": intelligent,
                  "gold_spent": gold_spent, "monster_attack": m_res,
                  "poison_tick": poison_tick, "player_dead": player_dead}
        self.last_parley_verdict = result
        return result

    def _npcs_in_room(self, room_id=None):
        """NPC objects present in a room (current room by default). The union of
        the legacy placement (an NPC whose registry key IS the room_id) and the
        new multi-NPC placement (registry keys listed in room['npcs'])."""
        if room_id is None:
            room_id = self._room_id()
        keys = set()
        if room_id in NPC_REGISTRY:
            keys.add(room_id)
        room = self._authoritative_room(room_id)
        for k in (room or {}).get("npcs") or []:
            keys.add(k)
        return [NPC_REGISTRY[k] for k in keys if k in NPC_REGISTRY]

    def talk_to_npc(self, name=None):
        room_id = self._room_id()
        npcs = self._npcs_in_room(room_id)
        if not npcs:
            return {"error": "There is no one here to talk to."}
        low = (name or "").strip().lower()
        if not low:
            if len(npcs) == 1:
                npc = npcs[0]
            else:
                return {"error": "Several people are here - who do you want to talk to?",
                        "choices": [n.name for n in npcs]}
        else:
            exact = [n for n in npcs if n.name.lower() == low]
            if exact:
                npc = exact[0]
            else:
                partial = [n for n in npcs if low in n.name.lower() or n.name.lower() in low]
                if len(partial) == 1:
                    npc = partial[0]
                elif len(partial) > 1:
                    return {"error": f"Several {name} here - which one?",
                            "choices": [n.name for n in partial]}
                else:
                    return {"error": f"There is no one called {name} here.",
                            "choices": [n.name for n in npcs]}
        knowledge = npc.hints.copy()
        result = {"npc_name": npc.name, "description": npc.description,
                  "personality_traits": npc.traits, "knowledge_and_hints": knowledge,
                  "instruction": "Roleplay this character using the traits provided. Remember that citizens of Luminaria have drifting memories."}
        if getattr(npc, "shop", None):
            result["shop"] = npc.shop
        result.update(self.talk_hook_quest(npc.name))
        return result

    # --- Monster drops ------------------------------------------------------
    DROP_ITEM_POOL = [
        ("Charcoal", 3), ("Dried Sage", 3), ("Crushed Quartz", 2), ("Mercury-Dew", 2),
        ("Red Potion", 1), ("Green Potion", 1), ("Blue Potion", 1),
        ("Rations, Preserved", 1), ("Torches (6)", 1), ("Arrows", 2),
    ]

    def roll_monster_drops(self, monster_name):
        """Kill rewards: stingy gold, rarer trinkets, and Monster Meat from
        flesh-and-blood bestiary entries. Rolls a loot bundle and returns it
        as {"gold": int, "items": [[name, qty], ...]} WITHOUT granting it -
        the spoils stay inside the corpse until the player searches it."""
        monster_id = self._find_monster(monster_name)
        mdata = self.world.generator.bestiary.get(monster_id, {})
        depth = abs(self.player.data["position"]["z"])
        gold = 0
        items = []
        if random.random() < 0.35:
            gold = random.randint(1, 3 + depth)
        if random.random() < 0.12:
            items.append(list(random.choice(self.DROP_ITEM_POOL)))
        if mdata.get("meat") and random.random() < 0.30:
            items.append(["Monster Meat", 1])
        return {"gold": gold, "items": items}

    # --- Corpses as containers --------------------------------------------
    # A slain monster leaves its corpse in the room. The corpse is a loot
    # container: nothing is granted until the player searches it, so loot can
    # be walked past, left behind, or come back for later.
    CORPSE_ALIASES = ("corpse", "corpses", "body", "bodies", "remains",
                      "carcass", "carcasses", "husk")

    def _authoritative_room(self, room_id):
        """The live room dict (not the copy get_room() hands out), dungeon
        first, then the town map."""
        for store in (self.world.rooms, getattr(self.world, "town_rooms", {})):
            room = store.get(room_id)
            if room is not None:
                return room
        return None

    def _save_room(self, room_id):
        """Persist the room's store - the dungeon save and/or the town map."""
        self.world.save()
        if room_id in getattr(self.world, "town_rooms", {}):
            with open(os.path.join(BASE_DIR, "data", "town_map.json"), "w") as f:
                json.dump(self.world.town_rooms, f, indent=1)

    # --- Virtue economy ---------------------------------------------------
    def _virtue_ledger(self):
        """Deeds ledger tracked between audiences with Lord Raika."""
        led = self.player.data.get("virtue_ledger")
        if not isinstance(led, dict):
            led = {"day": 0, "last_stand": 0, "floors": []}
            self.player.data["virtue_ledger"] = led
        led.setdefault("day", 0)
        led.setdefault("last_stand", 0)
        led.setdefault("floors", [])
        return led

    def _virtue_day(self):
        """Sleeps elapsed since the run began - the once-per-sleep gate clock."""
        return self._virtue_ledger()["day"]

    def _bump_virtue_day(self):
        self._virtue_ledger()["day"] += 1
        self.player.save()

    def alms(self, amount):
        """Give gold to the beggar in the Quiet Quarter. Once per sleep, and
        the gift must exceed your last accepted gift to earn virtue."""
        pos = self.player.data["position"]
        if f"{pos['x']}_{pos['y']}_{pos['z']}" != VIRTUE_DATA.get("beggar_room", "-1_0_0"):
            return {"success": False,
                    "message": "You find no one here worth your coin - and one of them is surely pretending."}
        led = self._virtue_ledger()
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            last = led.get("alms_last", 0)
            extra = (f" Your last accepted gift was {last} gold - the next one must exceed it."
                     if last else " The hat waits for a gift greater than nothing.")
            return {"success": False,
                    "message": ("How much do you give? (e.g. 'alms 5')." + extra)}
        if amount <= 0:
            return {"success": False, "message": "The beggar's hat does not tip itself."}
        gold = self.player.data["gold"]
        if amount > gold:
            return {"success": False,
                    "message": f"You only have {gold} gold - the hat sees right through you."}
        day = self._virtue_day()
        if day == led.get("alms_day", -1):
            self.player.update_gold(-amount)
            return {"success": True,
                    "message": f"You drop {amount} gold into the hat. It rings with amused "
                               f"indifference - heaven has already blessed this one today."}
        if amount <= led.get("alms_last", 0):
            self.player.update_gold(-amount)
            return {"success": True,
                    "message": f"You give {amount} gold. The beggar squints at the size of it - "
                               f"a gift greater than {led.get('alms_last')} catches heaven's eye."}
        self.player.update_gold(-amount)
        led["alms_day"] = day
        led["alms_last"] = amount
        self.player.save()
        self.player.update_virtue(1)
        return {"success": True,
                "message": f"You give {amount} gold - more than any gift before it. "
                           f"The beggar's eyes clear for a moment. (Virtue +1)"}

    def tip_virtue(self):
        """Virtue for a Flagon tip - once per sleep. True if granted."""
        led = self._virtue_ledger()
        day = self._virtue_day()
        if led.get("tip_day") == day:
            return False
        led["tip_day"] = day
        self.player.save()
        self.player.update_virtue(1)
        return True

    def confess(self):
        """Shrine coin slot: 50 gold for 1 virtue, once per sleep."""
        pos = self.player.data["position"]
        room = self.world.get_room(pos["x"], pos["y"], pos["z"])
        is_shrine = bool(room.get("shrine")) or room.get("type") == "Shrine"
        if not is_shrine:
            return {"success": False, "message": "There is no shrine here."}
        cost = VIRTUE_DATA.get("confess_cost", 50)
        led = self._virtue_ledger()
        day = self._virtue_day()
        if led.get("confess_day") == day:
            return {"success": False,
                    "message": "The slot has closed for the night. Confess again after you sleep."}
        if self.player.data["gold"] < cost:
            return {"success": False,
                    "message": f"The slot swallows nothing but {cost} gold - and your purse is lighter."}
        self.player.update_gold(-cost)
        led["confess_day"] = day
        self.player.save()
        self.player.update_virtue(1)
        return {"success": True,
                "message": f"You feed {cost} gold into the slot. It chews, considers, "
                           f"and forgives. (Virtue +1)"}

    def confer(self):
        """Lord Raika reads your deeds: +1 per new floor, +1 per last stand,
        capped (+3). Resets the ledger; once per sleep."""
        pos = self.player.data["position"]
        if f"{pos['x']}_{pos['y']}_{pos['z']}" != VIRTUE_DATA.get("confer_room", "0_2_0"):
            return {"success": False,
                    "message": "Lord Raika holds court at the Castle. Report there."}
        led = self._virtue_ledger()
        day = self._virtue_day()
        if led.get("confer_day") == day:
            return {"success": False,
                    "message": "Raika waves a silken hand - he has already held audience with "
                               "you today. Come back after you sleep."}
        floors = len(led.get("floors", []))
        stands = led.get("last_stand", 0)
        total = floors + stands
        granted = min(total, VIRTUE_DATA.get("confer_cap", 3))
        led["confer_day"] = day
        led["floors"] = []
        led["last_stand"] = 0
        self.player.save()
        if total == 0:
            return {"success": True, "granted": 0,
                    "message": "Raika listens to your report, or something adjacent to it. "
                               "'Delightful. Do send the wine cart.' (Virtue +0)"}
        self.player.update_virtue(granted)
        return {"success": True, "granted": granted,
                "message": ("Raika actually looks at you - briefly. He counts your deeds on "
                            f"silk-draped fingers: {floors} new floor(s), {stands} last stand(s). "
                            f"'Granted, granted.' (Virtue +{granted})")}

    # --- Quests ------------------------------------------------------------ #
    # One active quest at a time. Offers are rolled deterministically from
    # (world seed, NPC, day) so they survive reload and never touch the
    # global RNG stream that combat depends on.
    def _quest_book(self):
        """The quest ledger stored inside player.data (additive save key)."""
        qb = self.player.data.get("quest")
        if not isinstance(qb, dict):
            qb = {"active": None, "pending": None, "recent": [], "declined": []}
            self.player.data["quest"] = qb
        qb.setdefault("active", None)
        qb.setdefault("pending", None)
        qb.setdefault("recent", [])
        qb.setdefault("declined", [])
        return qb

    @staticmethod
    def _quest_monster_match(template_name, slain_name):
        """Word-set matching: 'Rat' hits 'Rat, Giant', and 'Giant Rat' matches
        its inverted bestiary spelling - but 'Goblin' does NOT hit 'Hobgoblin'."""
        tw = set(re.findall(r"[a-z]+", str(template_name).lower()))
        sw = set(re.findall(r"[a-z]+", str(slain_name).lower()))
        return bool(tw) and bool(sw) and (tw <= sw or sw <= tw)

    @staticmethod
    def _quest_objective_text(objective):
        if objective.get("monster"):
            return f"Slay {objective.get('count', 1)} x {objective['monster']}"
        return f"Bring {objective.get('qty', 1)} x {objective.get('item', '?')}"

    def _quest_progress(self, q):
        """Kill quests count stored progress; collect quests count the pack."""
        obj = q.get("objective", {})
        if obj.get("monster"):
            return int(q.get("progress", 0))
        have = self.player.data["inventory"].get(obj.get("item"), 0)
        return min(int(have), int(obj.get("qty", 1)))

    @staticmethod
    def _quest_turn_in_name(q):
        t = q.get("turn_in", "giver")
        if t == "auto":
            return None
        return q.get("giver") if t == "giver" else t

    def _quest_on_cooldown(self, qb, tid):
        days = int(QUEST_SETTINGS.get("recent_cooldown_days", 3))
        today = self.player.data.get("time", {}).get("day", 1)
        for rec in qb.get("recent", []):
            if rec.get("id") == tid and today - int(rec.get("day", 0)) <= days:
                return True
        return False

    def quest_candidates(self, npc_name):
        """Templates this NPC may offer right now: their own or 'any', minus
        the active quest, minus recently done/abandoned, minus what they
        offered and was declined today."""
        qb = self._quest_book()
        active_id = (qb.get("active") or {}).get("id")
        today = self.player.data.get("time", {}).get("day", 1)
        declined_today = {d.get("id") for d in qb.get("declined", [])
                          if d.get("npc") == npc_name and int(d.get("day", 0)) == today}
        out = []
        for t in QUEST_TEMPLATES:
            if t.get("giver") not in (npc_name, "any"):
                continue
            if t.get("id") == active_id or self._quest_on_cooldown(qb, t.get("id")):
                continue
            if t.get("id") in declined_today:
                continue
            out.append(t)
        return out

    def roll_quest_offer(self, npc_name):
        """Deterministic offer pick keyed to (seed, NPC, day)."""
        cands = self.quest_candidates(npc_name)
        if not cands:
            return None
        day = self.player.data.get("time", {}).get("day", 1)
        rng = random.Random(f"{self.world.seed}|{npc_name}|{day}")
        offer = dict(rng.choice(cands))
        offer["giver"] = npc_name
        return offer

    def talk_hook_quest(self, npc_name):
        """Called from talk_to_npc: attach active-quest context, or roll a
        pending offer when the quest slot is free. A pending offer from a
        different citizen is replaced - an offer waits for the one who made
        it."""
        qb = self._quest_book()
        result = {}
        if qb.get("active"):
            result["active_quest"] = self.quest_status()
        else:
            pending = qb.get("pending")
            if pending and pending.get("giver") != npc_name:
                qb["pending"] = None
            if qb.get("pending") is None:
                qb["pending"] = self.roll_quest_offer(npc_name)
                self.player.save()
            if qb.get("pending"):
                result["quest_offer"] = qb["pending"]
        return result

    def quest_status(self):
        """Structured view of the active quest (None when the slot is free)."""
        qb = self._quest_book()
        q = qb.get("active")
        if not q:
            return None
        obj = q.get("objective", {})
        need = int(obj.get("count") or obj.get("qty") or 1)
        prog = self._quest_progress(q)
        turn_name = self._quest_turn_in_name(q)
        return {
            "id": q.get("id"), "title": q.get("title"),
            "archetype": q.get("archetype", "task"),
            "text": q.get("text", ""),
            "objective": self._quest_objective_text(obj),
            "progress": prog, "needed": need,
            "done": prog >= need,
            "turn_in": "completes on the spot" if turn_name is None
                       else f"return to {turn_name}",
            "turn_in_npc": turn_name,
            "giver": q.get("giver"),
            "gold": int(q.get("gold", 0)),
        }

    def quest_pending(self):
        qb = self._quest_book()
        p = qb.get("pending")
        if not p:
            return None
        turn_name = self._quest_turn_in_name(p)
        return {"id": p.get("id"), "title": p.get("title"), "giver": p.get("giver"),
                "text": p.get("text", ""),
                "objective": self._quest_objective_text(p.get("objective", {})),
                "turn_in": "completes on the spot" if turn_name is None
                           else f"return to {turn_name}"}

    def accept_quest(self):
        qb = self._quest_book()
        if qb.get("active"):
            return {"success": False,
                    "message": f"You are already on a quest: '{qb['active'].get('title')}'. "
                               "Abandon it ('quest abandon') before taking another."}
        p = qb.get("pending")
        if not p:
            return {"success": False, "message": "No one has offered you a quest here."}
        qb["active"] = {
            "id": p.get("id"), "title": p.get("title"),
            "archetype": p.get("archetype", "task"), "text": p.get("text", ""),
            "objective": p.get("objective", {}),
            "turn_in": p.get("turn_in", "giver"), "giver": p.get("giver"),
            "gold": int(p.get("gold", 0)),
            "progress": 0,
            "accepted_day": self.player.data.get("time", {}).get("day", 1),
        }
        qb["pending"] = None
        self.player.save()
        return {"success": True,
                "message": f"Quest accepted: '{qb['active']['title']}' - "
                           f"{self._quest_objective_text(qb['active']['objective'])}."}

    def decline_quest(self):
        qb = self._quest_book()
        p = qb.get("pending")
        if not p:
            return {"success": False, "message": "No pending quest offer."}
        today = self.player.data.get("time", {}).get("day", 1)
        qb.setdefault("declined", []).append({"id": p.get("id"), "npc": p.get("giver"),
                                              "day": today})
        del qb["declined"][:-20]
        qb["pending"] = None
        self.player.save()
        return {"success": True,
                "message": f"You decline the offer: '{p.get('title')}'."}

    def abandon_quest(self):
        qb = self._quest_book()
        q = qb.get("active")
        if not q:
            return {"success": False, "message": "You have no active quest."}
        qb["active"] = None
        qb["pending"] = None
        today = self.player.data.get("time", {}).get("day", 1)
        qb.setdefault("recent", []).append({"id": q.get("id"), "end": "abandoned",
                                            "day": today})
        del qb["recent"][:-20]
        penalty = int(QUEST_SETTINGS.get("abandon_penalty", 5))
        v = int(self.player.data.get("virtue", 0))
        if v > 0:
            # The penalty never drags virtue below 0, and never makes an
            # already-negative virtue more negative (Anti-Shrine runs).
            self.player.data["virtue"] = max(0, v - penalty)
            note = f" Virtue -{min(penalty, v)}."
        else:
            note = " Your virtue is already in the dark; it sinks no further."
        self.player.save()
        return {"success": True,
                "message": f"Quest abandoned: '{q.get('title')}'. All progress is lost."
                           + note + f" (now {self.player.data['virtue']:+d})"}

    def notify_kill(self, monster_name):
        """Hook for the combat loop: count a slain monster toward a kill quest."""
        qb = self._quest_book()
        q = qb.get("active")
        if not q or not q.get("objective", {}).get("monster"):
            return None
        if not self._quest_monster_match(q["objective"]["monster"], monster_name):
            return None
        q["progress"] = int(q.get("progress", 0)) + 1
        self.player.save()
        need = int(q["objective"].get("count", 1))
        return (f"Quest: {q['objective']['monster']} slain "
                f"{min(q['progress'], need)}/{need}.")

    def check_quest_completion(self):
        """Called after every command. Completes when the objective is met AND
        the player stands at the turn-in point (or the quest needs no hand-in).
        Completion is the best virtue award: automatic +5."""
        qb = self._quest_book()
        q = qb.get("active")
        if not q:
            return None
        obj = q.get("objective", {})
        need = int(obj.get("count") or obj.get("qty") or 1)
        if self._quest_progress(q) < need:
            return None
        turn_name = self._quest_turn_in_name(q)
        if turn_name is not None:
            pos = self.player.data["position"]
            if NPC_ROOM_BY_NAME.get(turn_name) != f"{pos['x']}_{pos['y']}_{pos['z']}":
                return None
        if obj.get("item"):
            self.player.remove_item(obj["item"], int(obj.get("qty", 1)))
        reward = int(QUEST_SETTINGS.get("virtue_reward", 5))
        gold = int(q.get("gold", 0))
        qb["active"] = None
        qb["pending"] = None
        qb["declined"] = []
        today = self.player.data.get("time", {}).get("day", 1)
        qb.setdefault("recent", []).append({"id": q.get("id"), "end": "done",
                                            "day": today})
        del qb["recent"][:-20]
        self.player.update_virtue(reward)
        if gold:
            self.player.update_gold(gold)
        msg = (f"QUEST COMPLETE: '{q.get('title')}' - {q.get('giver')} "
               f"accepts your service. (Virtue +{reward}"
               + (f", +{gold} gold" if gold else "") + ")")
        return {"success": True, "message": msg, "virtue": reward, "gold": gold}

    # --- Discovery rooms --------------------------------------------------
    FOUNTAIN_OUTCOMES = [
        ("Enchanted Water", 20, "The water is faintly luminescent and tastes of cold starlight."),
        ("Holy Water", 20, "The water is clean and sharp, and something in it answers the fear in you."),
        ("Blood", 15, "It is red, and it is thick, and it is definitely blood."),
        ("Refreshing Water", 25, "It is only water - cold, clear and impossibly refreshing."),
        ("Rancid Water", 20, "It hits your stomach a half-second before your mind decides it was water."),
    ]
    POOL_OUTCOMES = [
        ("Water", 40, "It is just water - cold, still, and faintly cold in ways you can't name."),
        ("Iridescent Rainbow liquid", 25, "The liquid shimmers like oil on a wet street, throwing tiny rainbows."),
        ("Sewage", 20, "It is sewage. You waded into sewage. Somewhere above you, something laughs."),
        ("Ichor", 15, "The liquid is golden and slow, like honey with a pulse in it."),
    ]

    def _discovery_ctx(self):
        """(room_id, room_state, discovery_name) when standing in a live dungeon
        Discovery room, else (None, None, None)."""
        pos = self.player.data["position"]
        room_id = f"{pos['x']}_{pos['y']}_{pos['z']}"
        state = self.world.rooms.get(room_id)
        if state and state.get("type") == "Discovery" and state.get("discovery"):
            return room_id, state, state["discovery"]
        return None, None, None

    def _grant_loot(self, loot):
        """Give a {item, gold} loot dict to the player; returns message lines."""
        lines = []
        if loot.get("gold"):
            self.player.update_gold(loot["gold"])
            lines.append(f"You pocket {loot['gold']} gold.")
        if loot.get("item"):
            res = self.player.add_item(loot["item"])
            if isinstance(res, dict) and not res.get("success"):
                lines.append(f"{loot['item']} - but {res['message']}")
            else:
                lines.append(f"You take the {loot['item']}.")
        if not lines:
            lines.append("Nothing but dust.")
        return lines

    def _roll_reagent_bundle(self, units):
        """Distribute `units` reagent units randomly across the common/uncommon
        pool. Returns dict {reagent: qty}."""
        pool = [k for k, v in REAGENT_DATA.items()
                if v.get("rarity") in ("Common", "Uncommon")]
        bundle = {}
        for _ in range(units):
            r = random.choice(pool) if pool else "Charcoal"
            bundle[r] = bundle.get(r, 0) + 1
        return bundle

    def _grant_reagent_bundle(self, bundle):
        """Add a {reagent: qty} bundle to inventory. Returns message lines."""
        lines, refused = [], []
        for name, qty in sorted(bundle.items()):
            res = self.player.add_item(name, qty)
            if isinstance(res, dict) and not res.get("success"):
                refused.append(f"{qty} {name}")
            else:
                lines.append(f"{qty} x {name}")
        text = ", ".join(lines) if lines else "nothing you can carry"
        msg = f"You gather reagents: {text}."
        if refused:
            msg += " Left behind: " + ", ".join(refused) + "."
        return [msg]

    def _apply_disease(self):
        effects = self.player.data.setdefault("status_effects", [])
        if "Diseased" in effects:
            return "You are already diseased; the malaise deepens but nothing changes."
        effects.append("Diseased")
        self.player.data["max_hp"] = 4
        if self.player.data["hp"] > 4:
            self.player.data["hp"] = 4
        self.player.save()
        return "A sickening malaise settles into your bones. (Diseased - max HP reduced to 4)"

    def _cure_disease(self):
        effects = self.player.data.setdefault("status_effects", [])
        if "Diseased" not in effects:
            return "You feel no sickness to be cured - but the meditation does you good."
        effects.remove("Diseased")
        self.player.refresh_derived_stats()  # max_hp back to normal, hp restored
        self.player.save()
        return "The feverish haze lifts and strength floods back into your limbs. (Diseased cured - HP restored)"

    def _adjust_stat(self, delta_map):
        """Apply {stat: +/-n} permanently; keeps HP sane across max-HP refresh."""
        hp_before = self.player.data["hp"]
        for stat, delta in delta_map.items():
            self.player.data["stats"][stat] = self.player.data["stats"].get(stat, 10) + delta
        self.player.refresh_derived_stats()
        new_max = self.player.data["max_hp"]
        self.player.data["hp"] = max(1, min(hp_before, new_max))
        self.player.save()

    def discovery_search(self):
        room_id, state, name = self._discovery_ctx()
        if not state:
            return "You search the area but find nothing unusual."
        ds = state.setdefault("discovery_state", {})
        flavor = DISCOVERY_DATA.get(name, {}).get("search", "You search, carefully.")

        if name == "Magic Circle":
            if ds.get("searched"):
                return "The circle is empty now; you have already taken what it offered."
            ds["searched"] = True
            self.world.save()
            roll = random.randint(1, 3)
            if roll == 1:
                spell = random.choice(list(SPELL_DATA.keys()))
                res = self.player.add_item(f"Page: {spell}")
                ok = not (isinstance(res, dict) and not res.get("success"))
                self.player.update_virtue(1)
                return (f"{flavor} At the circle's center, a page lies folded: a spell "
                        f"recipe for {spell}." + ("" if ok else " (You cannot carry it - it flutters back down.)"))
            if roll == 2:
                spell = random.choice(list(SPELL_DATA.keys()))
                res = self.player.add_item(f"Scrap: {spell}")
                ok = not (isinstance(res, dict) and not res.get("success"))
                return (f"{flavor} A torn scrap, inked on one corner of the circle: "
                        f"{spell}." + ("" if ok else " (You cannot carry it - it flutters back down.)"))
            n = random.randint(1, 4)
            lines = [flavor + f" Nestled in the inlay you find reagents - {n} unit(s)."]
            lines += self._grant_reagent_bundle(self._roll_reagent_bundle(n))
            return " ".join(lines)

        if name == "Abandoned Campsite":
            if ds.get("searched"):
                return "The campsite has given up everything it had."
            ds["searched"] = True
            self.world.save()
            n = random.randint(1, 3)
            lines = [f"{flavor} Beneath a collapsed bedroll you find {n} preserved ration(s)."]
            res = self.player.add_item("Rations, Preserved", n)
            if isinstance(res, dict) and not res.get("success"):
                lines.append(res["message"])
            else:
                lines.append(f"(+{n} Rations, Preserved)")
            return " ".join(lines)

        if name == "Dead Body":
            if ds.get("searched"):
                return "The body lies still again. You have already emptied it."
            ds["searched"] = True
            self.world.save()
            weapon = random.choice(list(WEAPON_DATA.keys()))
            armor_pool = [a for a in ARMOR_DATA if "Shield" not in a]
            armor = random.choice(armor_pool)
            lines = [f"{flavor} You kneel and go through their effects - your effects - with shaking hands."]
            for item in (weapon, armor):
                res = self.player.add_item(item)
                if isinstance(res, dict) and not res.get("success"):
                    lines.append(f"{item} - but {res['message']}")
                else:
                    lines.append(f"You take the {item}.")
            lines += self._grant_reagent_bundle(self._roll_reagent_bundle(12))
            return " ".join(lines)

        if name == "Pool":
            liquid = ds.get("liquid", "Water")
            if not ds.get("waded"):
                if ds.get("has_loot"):
                    return (f"{flavor} Something glints on the bottom of the {liquid.lower()}, "
                            "but you'll have to get in the water (try 'wade') to be sure.")
                return flavor + " Nothing disturbs the surface."
            if ds.get("has_loot") and not ds.get("loot_taken"):
                ds["loot_taken"] = True
                self.world.save()
                loot = self.world.generator._stock_loot(abs(self.player.data["position"]["z"]))
                lines = [f"{flavor} Your hand closes on something on the bottom."]
                lines += self._grant_loot(loot)
                return " ".join(lines)
            if ds.get("loot_taken"):
                return "The pool keeps nothing else from you."
            return flavor + " Nothing but the bottom."

        # Archway / Altar / Fountain default
        return flavor

    def discovery_drink(self):
        room_id, state, name = self._discovery_ctx()
        if not state:
            return "There is nothing here to drink from."
        if name != "Fountain":
            return "There is nothing here to drink from."
        ds = state.setdefault("discovery_state", {})
        if not ds.get("liquid"):
            roll = random.randint(1, 100)
            acc = 0
            for lname, weight, _flav in self.FOUNTAIN_OUTCOMES:
                acc += weight
                if roll <= acc:
                    ds["liquid"] = lname
                    break
            ds.setdefault("liquid", "Refreshing Water")
            self.world.save()
        liquid = ds["liquid"]
        if liquid == "Enchanted Water":
            healed = self.player.update_hp(4)
            self.player.update_satiety(10)
            extra = "" if isinstance(healed, int) else " " + str(healed)
            return ("You cup the luminescent water and drink. Warmth knits through you, "
                    "and for a moment you are not even hungry. (+4 HP, +10 Satiety)" + extra)
        if liquid == "Holy Water":
            msg = self._cure_disease()
            if not ds.get("virtue_given"):
                ds["virtue_given"] = True
                self.world.save()
                self.player.update_virtue(1)
                return ("You drink. It is clean, and sharp, and answers the fear in you. "
                        "Something, somewhere, notices. (Virtue +1) " + msg)
            return "You drink again. It is still holy, still sharp. " + msg
        if liquid == "Blood":
            return ("You drink, because you are thirsty enough to. It is blood - "
                    "someone else's - and it is neither help nor harm.")
        if liquid == "Refreshing Water":
            self.player.update_satiety(10)
            return "You drink deeply. It is only water, and it is wonderful. (+10 Satiety)"
        # Rancid
        msg = self._apply_disease()
        return "You drink, and regret it instantly. " + msg

    def discovery_wade(self):
        room_id, state, name = self._discovery_ctx()
        if not state:
            return "There is no pool here to wade into."
        if name != "Pool":
            return "There is no pool here to wade into."
        ds = state.setdefault("discovery_state", {})
        if not ds.get("liquid"):
            roll = random.randint(1, 100)
            acc = 0
            for lname, weight, _flav in self.POOL_OUTCOMES:
                acc += weight
                if roll <= acc:
                    ds["liquid"] = lname
                    break
            ds.setdefault("liquid", "Water")
            ds["has_loot"] = random.random() < 0.25
            self.world.save()
        liquid = ds["liquid"]
        already = ds.get("waded")
        ds["waded"] = True
        self.world.save()
        repeat = " You are already soaked, but you wade again anyway." if already else ""
        if liquid == "Water":
            if not already and not ds.get("virtue_given"):
                ds["virtue_given"] = True
                self.world.save()
                self.player.update_virtue(1)
                return ("You wade in to your knees. It is just water. "
                        "It is cold, and ordinary, and that is a kind of mercy. (Virtue +1)" + repeat)
            return ("You wade in to your knees. It is just water. "
                    "It is cold, and ordinary, and that is a kind of mercy." + repeat)
        if liquid == "Iridescent Rainbow liquid":
            if not already:
                stats = list(self.player.data["stats"].keys())
                up, down = random.sample(stats, 2)
                self._adjust_stat({up: 1, down: -1})
                return (f"The rainbow liquid climbs your skin and sinks in. "
                        f"({up} +1, {down} -1)" + repeat)
            return ("The rainbow liquid has nothing further to give you." + repeat)
        if liquid == "Sewage":
            if not already:
                msg = self._apply_disease()
                return "You wade in sewage up to your knees. " + msg + repeat
            return "You are already steeped in sewage." + repeat
        # Ichor
        if not already:
            save = self._roll_poison_save(dc=12)
            if save.get("applied") and not save.get("already_poisoned"):
                extra = "The golden liquid bites like wasps. (Poisoned)"
            elif save.get("applied") or save.get("refreshed"):
                extra = "The ichor stings where the old poison still works. (Poison lingers)"
            else:
                extra = (f"The golden liquid bites, but your blood resists it "
                         f"(save {save.get('roll')} vs DC 12).")
            return extra + repeat
        return "The ichor has already marked you." + repeat

    def discovery_meditate(self):
        room_id, state, name = self._discovery_ctx()
        if not state:
            return "There is no shrine here to meditate at."
        if name != "Holy Altar":
            return "You could meditate anywhere, I suppose - but nothing answers here."
        msg = self._cure_disease()
        self.player.update_virtue(1)
        return ("You kneel at the altar and are still for a long while. "
                "Something, somewhere, notices. (Virtue +1) " + msg)

    def discovery_enter(self):
        room_id, state, name = self._discovery_ctx()
        if not state:
            return "There is nothing here to enter."
        if name != "Mysterious Archway":
            return "There is nothing here to step through."
        ds = state.setdefault("discovery_state", {})
        if ds.get("used"):
            return ("The archway stands dark and still; it has spent its power for now. "
                    "The word HOME looks like a joke someone else is in on.")
        ds["used"] = True
        self.world.save()
        self.player.update_virtue(1)
        self.player.data["position"] = {"x": -1, "y": 1, "z": 0}
        self.player.data["last_direction"] = None
        self.player.save()
        label = self.time_label()
        return ("You step through the archway - through nothing, through cold, through "
                "the smell of incense - and the Temple of Radiance closes around you "
                "like a remembered sentence. (Virtue +1)"
                + (f" It is {label}." if label else ""))

    def search_traps(self):
        pos = self.player.data["position"]
        room_id = f"{pos['x']}_{pos['y']}_{pos['z']}"
        room = self.world.get_room(pos["x"], pos["y"], pos["z"])
        # Searching costs an hour of careful work.
        self.player.update_satiety(-1)

        if not room.get("trap"):
            if random.random() < 0.05:
                return ("You poke around for an hour and find nothing but dust. "
                        "Somehow, you are more tired than before.")
            return "You search the area carefully but find no signs of traps."

        trap_type = room.get("trap_type", "Pressure Plate (Darts)")
        dc = TRAP_DATA.get(trap_type, {}).get("dc", 12)
        roll = self.roll_dice(20, max(self.get_stat_bonus("WIS"), self.get_stat_bonus("INT")))
        if roll >= dc:
            if room_id in self.world.rooms:
                self.world.rooms[room_id]["trap_spotted"] = True
                self.world.save()
            return f"Success! You spotted a {trap_type} hidden in the floor/walls."
        if roll <= 5:
            # Fumble: you go poking at a thing you cannot see, and the thing bites.
            res = self._resolve_trap(trap_type)
            if res["triggered"]:
                if room_id in self.world.rooms:
                    self.world.rooms[room_id]["trap"] = False
                    self.world.save()
                msg = (f"You go too deep with your probing fingers and set the "
                       f"{trap_type} off yourself! {res['msg']}")
                self.player.update_hp(0)
                return msg
            return (f"You go too deep with your probing fingers and nearly set the "
                    f"{trap_type} off - it holds.")
        return "You search the room carefully, but find no traps."

    # --- Detect Magic ---------------------------------------------------- #
    def _room_state(self):
        """Persisted room dict (dungeon or town), or {} - no lazy generation."""
        pos = self.player.data["position"]
        rid = f"{pos['x']}_{pos['y']}_{pos['z']}"
        return self.world.rooms.get(rid) or self.world.town_rooms.get(rid) or {}

    def _magic_liquid(self, ds):
        """(name, entry) for the liquid in a fountain/pool, rolling its nature
        on first probe - so Detect Magic can 'know' it before anyone drinks."""
        name = ds.get("liquid")
        if not name:
            table = (self.FOUNTAIN_OUTCOMES if ds.get("_kind") == "Fountain"
                     else self.POOL_OUTCOMES)
            roll = random.randint(1, 100)
            acc = 0
            for lname, weight, _flav in table:
                acc += weight
                if roll <= acc:
                    name = lname
                    break
            name = name or table[0][0]
            ds["liquid"] = name
            if ds.get("_kind") == "Pool" and "has_loot" not in ds:
                ds["has_loot"] = random.random() < 0.25
            self.world.save()
        return name, MAGIC_DATA.get("magic_liquids", {}).get(name)

    def scan_magic(self):
        """Deterministic aura scan of the current room. Facts only - the prose
        is the narrator's job."""
        pos = self.player.data["position"]
        rid = f"{pos['x']}_{pos['y']}_{pos['z']}"
        state = self.world.rooms.get(rid)
        town = state is None
        room = self.world.get_room(pos["x"], pos["y"], pos["z"])
        auras = []

        if town:
            places = MAGIC_DATA.get("town_places", {})
            entry = places.get(rid)
            if entry:
                auras.append({"source": entry.get("source", "this place"),
                              "intensity": entry.get("intensity", "strong"),
                              "kind": entry.get("kind", "a hallowed working")})
            elif room.get("shrine") or room.get("type") == "Shrine":
                auras.append({"source": "the shrine ground",
                              "intensity": "steady",
                              "kind": "consecrated, kept, and swept"})

        if not town:
            _d, ob = self._find_obstacle_way(room)
            ob = ob.get("name") if ob else None
            if ob:
                entry = MAGIC_DATA.get("magic_obstacles", {}).get(ob)
                if entry:
                    auras.append({"source": f"the {ob}",
                                  "intensity": entry.get("intensity", "strong"),
                                  "kind": entry.get("kind", "a working, still active"),
                                  "hint": entry.get("hint")})

            if room.get("type") == "Discovery" and room.get("discovery"):
                dname = room["discovery"]
                ds = room.setdefault("discovery_state", {})
                ds["_kind"] = dname
                if dname in ("Fountain", "Pool"):
                    lname, lent = self._magic_liquid(ds)
                    if lent:
                        auras.append({"source": f"the {dname.lower()}",
                                      "intensity": lent.get("intensity", "strong"),
                                      "kind": f"its liquid is {lent.get('kind', 'unnamed')}",
                                      "named": lname})
                    else:
                        auras.append({"source": f"the {dname.lower()}",
                                      "intensity": "mundane",
                                      "kind": "its liquid is plain, and always was"})
                else:
                    entry = MAGIC_DATA.get("magic_discoveries", {}).get(dname)
                    if entry:
                        auras.append({"source": f"the {dname.lower()}",
                                      "intensity": entry.get("intensity", "strong"),
                                      "kind": entry.get("kind", "a working, still active"),
                                      "hint": entry.get("hint")})

            for monster in self._normalize_encounter(room.get("encounter")):
                mid = self._find_monster(monster)
                m = self.world.generator.bestiary.get(mid, {})
                sp = m.get("special", []) or []
                named = mid in set(MAGIC_DATA.get("magical_creatures", []))
                if named or m.get("magical") or ({"petrify", "invisible", "regenerate", "sleep_1"} & set(sp)):
                    auras.append({"source": f"the {monster}",
                                  "intensity": "strong",
                                  "kind": "an animate working - it is not alive, "
                                           "or not only alive"})

            cont = room.get("container") or (room.get("chest") or {}).get("item")
            if cont and (room.get("loot") or room.get("chest")) and not room.get("loot_taken"):
                auras.append({"source": f"the {room.get('container') or 'chest'}",
                              "intensity": "faint",
                              "kind": "a stored treasure, unopened, leaching a little magic"})

            corpses = self._clear_all_corpses(rid)
            if any((not c.get("searched")) and ((int((c.get("loot") or {}).get("gold", 0) or 0) > 0
                    or (c.get("loot") or {}).get("items") or [])) for c in corpses):
                auras.append({"source": "the corpse(s) here",
                              "intensity": "faint",
                              "kind": "spoils still held within, unclaimed"})

            if not room.get("echo_detected") and random.random() < float(MAGIC_DATA.get("echo_chance", 0.08)):
                room["echo_detected"] = True
                self.world.save()
                echo = MAGIC_DATA.get("echo", {"intensity": "faint",
                                               "kind": "an old enchantment, spent and purposeless"})
                auras.append({"source": "something buried here",
                              "intensity": echo.get("intensity", "faint"),
                              "kind": echo.get("kind", "an old enchantment, spent and purposeless")})

        return {"room": room.get("name", "Unknown Location"),
                "in_town": town,
                "auras": auras,
                "reading": self._magic_reading(auras)}

    @staticmethod
    def _cap_source(src):
        """Capitalise a source phrase without shouting proper nouns."""
        if not src:
            return src
        if src[:4] in ("the ", "The "):
            return src[0].upper() + src[1:]
        return src[0].upper() + src[1:]

    @staticmethod
    def _magic_reading(auras):
        if not auras:
            return ("You open your senses to the arcane, and find nothing. "
                    "This room is as mundane as a doorstep.")
        if len(auras) == 1:
            a = auras[0]
            if a["intensity"] == "mundane":
                line = (f"{GameEngine._cap_source(a['source'])} registers as mundane - {a['kind']}.")
            else:
                line = f"{GameEngine._cap_source(a['source'])} - {a['intensity']} magic: {a['kind']}."
            if a.get("hint"):
                line += f" ({a['hint']})"
            return line
        lines = [f"{i}. {a['source']} - {a['intensity']}: {a['kind']}"
                 + (f" ({a['hint']})" if a.get("hint") else "")
                 for i, a in enumerate(auras, 1)]
        return "Your detect magic finds {} reading(s): {}".format(
            len(auras), " | ".join(lines))

    def detect_magic(self):
        """Cast Detect Magic: report what registers."""
        res = self.scan_magic()
        return {"success": True, "spell": "Detect Magic",
                "auras": res["auras"], "reading": res["reading"],
                "message": "Detect Magic: " + res["reading"]}

    def disarm_trap(self):
        pos = self.player.data["position"]
        room_id = f"{pos['x']}_{pos['y']}_{pos['z']}"
        room = self.world.get_room(pos["x"], pos["y"], pos["z"])
        if not room.get("trap") or room.get("disarmed"):
            return "There is no active trap here to disarm."
        trap_type = room.get("trap_type", "Pressure Plate (Darts)")
        dc = TRAP_DATA.get(trap_type, {}).get("dc", 12)
        roll = self.roll_dice(20, self.get_stat_bonus("DEX"))
        if roll >= dc:
            if room_id in self.world.rooms:
                self.world.rooms[room_id]["disarmed"] = True
                self.world.save()
            return f"Click... snap. You successfully disarmed the {trap_type}."
        trap_info = TRAP_DATA.get(trap_type, {})
        damage = trap_info.get("damage", 2)
        self.player.update_hp(-damage)
        return f"Fumble! You triggered the {trap_type} while trying to disarm it and took {damage} damage!"

    def _find_obstacle_way(self, room, direction_word=None):
        """Locate an obstacle way in this room, optionally by direction."""
        want = self._resolve_direction(direction_word)
        for d, ob in room.get("obstacles", {}).items():
            if want is None or d == want:
                return d, ob
        return None, None

    def interact_environment(self, action, stat=None, idea_bonus=None):
        pos = self.player.data["position"]
        room = self.world.get_room(pos["x"], pos["y"], pos["z"])
        action_lower = str(action).lower()
        direction, ob = self._find_obstacle_way(room)
        if ob is None:
            return "There is nothing here that requires a special interaction."
        obstacle = ob.get("name", "obstacle")
        description = OBSTACLE_DATA.get(obstacle, "An obstacle blocks the way.")
        if any(w in action_lower for w in ("examine", "inspect", "study", "look", "describe")):
            return f"You examine the {obstacle}: {description}"
        # The LLM picks the stat and judges the idea; the engine validates both
        # and rolls the die. (Keyword fallback keeps the check usable if the
        # LLM omits a stat.)
        valid_stats = set(self.player.data["stats"].keys())
        if stat and str(stat).upper() in valid_stats:
            stat = str(stat).upper()
        elif any(w in action_lower for w in ("rubble", "force", "push", "pull", "break", "crowbar", "portcullis")):
            stat = "STR"
        elif any(w in action_lower for w in ("jump", "leap", "balance", "squeeze", "climb", "gap")):
            stat = "DEX"
        elif any(w in action_lower for w in ("magic", "spell", "reagent", "chant", "ward", "barrier", "elemental")):
            stat = "INT"
        else:
            stat = "WIS"
        try:
            bonus = int(idea_bonus)
        except (TypeError, ValueError):
            bonus = 0
        bonus = max(-5, min(5, bonus))
        roll = self.roll_dice(20, self.get_stat_bonus(stat) + bonus)
        dc = 12
        bonus_note = f"{bonus:+d}" if bonus else "\u00b10"
        if roll >= dc:
            return (f"Check: {action} using {stat} {bonus_note} against DC {dc} succeeded ({roll}). "
                    "The obstacle has not been cleared automatically; call clear_obstacle() if the solution should finish the puzzle.")
        return (f"Check: {action} using {stat} {bonus_note} against DC {dc} failed ({roll}). The obstacle still blocks the way.")

    def clear_obstacle(self, obstacle_name=None):
        pos = self.player.data["position"]
        key = f"{pos['x']}_{pos['y']}_{pos['z']}"
        room = self.world.rooms.get(key)
        if room is None:
            return "There is no obstacle here to clear."
        direction, ob = self._find_obstacle_way(room)
        if ob is None:
            return "There is no obstacle here to clear."
        obstacle = ob.get("name", "obstacle")
        if obstacle_name:
            supplied = str(obstacle_name).lower()
            if supplied not in obstacle.lower():
                return f"The obstacle here is {obstacle}, not {obstacle_name}."
        pair = self._edge_rooms(key, direction)
        if pair is None:
            room["obstacles"].pop(direction, None)
            self.world.save()
        else:
            self._promote_edge(pair, direction, rid=key)
            self.world.save()
        # Cleverness and tenacity are themselves virtues: solving an
        # environmental puzzle/obstacle earns a point of Virtue. (Only the
        # successful-clear path reaches here, so it can never double-count.)
        self.player.update_virtue(1)
        return f"The {obstacle} is cleared. (Virtue +1)"

    # --- doors & obstacles: the way is the thing (dungeon only) ---
    DIR_ALIASES = {"n": "North", "north": "North", "s": "South", "south": "South",
                   "e": "East", "east": "East", "w": "West", "west": "West"}

    def _resolve_direction(self, word):
        if not word:
            return None
        return self.DIR_ALIASES.get(str(word).strip().lower())

    def _edge_rooms(self, rid, direction):
        """Live room dicts on both sides of an edge, or None."""
        near = self.world.rooms.get(rid)
        if not near:
            return None
        entry = (near.get("exits", {}).get(direction)
                 or near.get("doors", {}).get(direction, {}).get("to")
                 or near.get("obstacles", {}).get(direction, {}).get("to"))
        far = self.world.rooms.get(entry) if entry else None
        if far is None:
            return None
        back = {"North": "South", "South": "North", "East": "West", "West": "East"}.get(direction)
        return near, far, back

    def _promote_edge(self, pair, direction, rid=None):
        """Move an edge into 'exits' on both sides (idempotent)."""
        near, far, back = pair
        if rid is None:
            pos = self.player.data["position"]
            rid = f"{pos['x']}_{pos['y']}_{pos['z']}"
        far_id = near["exits"].get(direction) or near["doors"].get(direction, {}).get("to") \
            or near["obstacles"].get(direction, {}).get("to")
        near["doors"].pop(direction, None)
        near["obstacles"].pop(direction, None)
        near["exits"][direction] = far_id
        far["doors"].pop(back, None)
        far["obstacles"].pop(back, None)
        far["exits"][back] = rid

    def open_door(self, direction_word):
        """'open <dir>' - closed doors yield freely; locked ones want a key
        or a lockpick. Obstacles answer with a hint, not a hinge."""
        pos = self.player.data["position"]
        rid = f"{pos['x']}_{pos['y']}_{pos['z']}"
        room = self.world.rooms.get(rid)
        if room is None:
            return "No door here - you are in town."
        direction = self._resolve_direction(direction_word)
        if not direction:
            return "Which way? (north, south, east, west)"
        doors = room.setdefault("doors", {})
        door = doors.get(direction)
        if door is None:
            ob = room.get("obstacles", {}).get(direction)
            if ob:
                _name = ob.get('name', 'obstacle')
            _verb = "jump" if _name in self.GAP_OBSTACLES else (
                "bash" if _name in self.BASH_OBSTACLES else "improvise")
            return (f"The {_name} bars the way to the {direction} - "
                    f"no hinge, no handle. Try '{_verb} {direction.lower()}' (or improvise).")
            return f"No door to the {direction}."
        pair = self._edge_rooms(rid, direction)
        if pair is None:
            return "The door leads nowhere you can reach."
        if door.get("state") == "closed":
            self._promote_edge(pair, direction)
            self.world.save()
            return f"You open the door to the {direction}."
        # locked
        inventory = self.player.data["inventory"]
        found_key = next((k for k in KEY_DATA if inventory.get(k, 0) > 0), None)
        if found_key:
            self.player.remove_item(found_key, 1)
            door["state"] = "closed"
            pair[1]["doors"].get(pair[2], {})["state"] = "closed"
            self.world.save()
            return f"You turn the {found_key} (consumed) - the door to the {direction} stands open."
        if inventory.get("Lockpick", 0) > 0:
            floor_level = abs(int(pos["z"]))
            dc = 11 + floor_level
            self.player.update_satiety(-1)
            roll = self.roll_dice(20, self.get_stat_bonus("DEX"))
            if roll >= dc:
                door["state"] = "closed"
                pair[1]["doors"].get(pair[2], {})["state"] = "closed"
                self.world.save()
                return (f"You coax the tumblers with your lockpick (roll {roll} vs DC {dc}) - "
                        f"the door to the {direction} stands open.")
            snap = ""
            if random.random() < 0.5:
                self.player.remove_item("Lockpick", 1)
                snap = " The lockpick snaps!"
            return f"You fiddle with the lock (roll {roll} vs DC {dc}) but it won't give.{snap}"
        return f"The door to the {direction} is locked. You'll need a key or a lockpick."

    def _ambush(self, z, rid):
        """Spawn the floor's stock monster in the room - noise draws a
        predator. Returns the monster's name."""
        monster = self.world.generator._stock_monster(z)
        room = self.world.rooms.get(rid)
        if room is not None:
            room["encounter"] = monster
            self.world.save()
        return monster

    def _bash_check(self, z, rid, label, on_success):
        """STR check (DC 12) to smash {label} open. The attempt is NOISY:
        on a 1-in-6 roll an ambush draws in regardless of the bash result."""
        self.player.update_satiety(-1)
        roll = self.roll_dice(20, self.get_stat_bonus("STR"))
        dc = 12
        ambushed = self._ambush(z, rid) if self.roll_dice(6) == 1 else None
        if roll >= dc:
            on_success()
            text = f"You bash {label} open (STR {roll} vs DC {dc})."
        else:
            text = f"You bash {label}, but it holds (STR {roll} vs DC {dc})."
        if ambushed:
            text += f" The noise draws a {ambushed}!"
        return text

    def jump(self, target):
        """'jump <way>' - leap a broken floor. A plain DEX check (DC 12):
        no insight, no LLM, no improvising - just legs and nerve."""
        pos = self.player.data["position"]
        rid = f"{pos['x']}_{pos['y']}_{pos['z']}"
        room = self.world.rooms.get(rid)
        if room is None:
            return {"text": "Nothing to jump here - you are in town."}
        direction = self._resolve_direction(target)
        if not direction:
            return {"text": "Which way? (north, south, east, west)"}
        ob = room.get("obstacles", {}).get(direction)
        if ob is None:
            return {"text": f"There is nothing to jump to the {direction}."}
        name = ob.get("name", "obstacle")
        if name not in self.GAP_OBSTACLES:
            return {"text": f"You can't jump the {name} to the {direction}."}
        self.player.update_satiety(-1)
        roll = self.roll_dice(20, self.get_stat_bonus("DEX"))
        dc = 12
        if roll >= dc:
            pair = self._edge_rooms(rid, direction)
            if pair is None:
                return {"text": f"You leap the {name} (DEX {roll} vs DC {dc}) - "
                                f"but the far side is unreachable."}
            self._promote_edge(pair, direction)
            self.world.save()
            return {"text": f"You leap the {name} (DEX {roll} vs DC {dc}) - "
                            f"the way to the {direction} is open.",
                    "moved": True, "direction": direction}
        return {"text": f"You lunge at the {name} (DEX {roll} vs DC {dc}) "
                        f"and fall short."}

    def bash(self, target=None):
        """'bash' / 'bash <way>' - smash a locked chest (or a locked door,
        or a cave-in / portcullis) open. A STR check (DC 12). The tradeoff:
        it is NOISY - a 1-in-6 chance of activating an ambush."""
        pos = self.player.data["position"]
        rid = f"{pos['x']}_{pos['y']}_{pos['z']}"
        z = pos["z"]
        room = self.world.rooms.get(rid)
        if room is None:
            return {"text": "Nothing to bash here - you are in town."}
        if not target:
            chest = room.get("chest")
            if not chest:
                return {"text": "There is no chest here to bash."}
            if not chest.get("locked", False):
                return {"text": "The chest is not locked - just open it."}
            text = self._bash_check(z, rid, "the chest",
                                    lambda: chest.update({"locked": False}))
            self.world.save()
            return {"text": text}
        direction = self._resolve_direction(target)
        if not direction:
            return {"text": "Bash what? A chest, or a way (north, south, east, west)."}
        door = room.get("doors", {}).get(direction)
        if door is not None:
            if door.get("state") != "locked":
                return {"text": f"The door to the {direction} is not locked - "
                                f"just open it."}
            pair = self._edge_rooms(rid, direction)
            if pair is None:
                return {"text": f"The door to the {direction} leads nowhere "
                                f"you can reach."}
            text = self._bash_check(z, rid, f"the door to the {direction}",
                                    lambda: self._promote_edge(pair, direction))
            self.world.save()
            return {"text": text}
        ob = room.get("obstacles", {}).get(direction)
        if ob is not None:
            name = ob.get("name", "obstacle")
            if name not in self.BASH_OBSTACLES:
                return {"text": f"You can't bash the {name} to the {direction}."}
            pair = self._edge_rooms(rid, direction)
            if pair is None:
                return {"text": f"The {name} leads nowhere you can reach."}
            text = self._bash_check(z, rid, f"{name} to the {direction}",
                                    lambda: self._promote_edge(pair, direction))
            self.world.save()
            return {"text": text}
        return {"text": f"There is nothing to bash to the {direction}."}

    def open_container(self, container_name=None):
        pos = self.player.data["position"]
        key = f"{pos['x']}_{pos['y']}_{pos['z']}"
        room = self.world.get_room(pos["x"], pos["y"], pos["z"])
        container_visual = room.get("container")
        chest_data = room.get("chest")
        loot_data_raw = room.get("loot")
        active_loot_source = None
        is_locked = False
        if chest_data:
            active_loot_source = chest_data.get("item")
            is_locked = chest_data.get("locked", False)
        elif loot_data_raw:
            active_loot_source = loot_data_raw
            is_locked = False
        corpses = self._clear_all_corpses(key)
        if container_name:
            if self._resolve_direction(container_name):
                return self.open_door(container_name)
            if any(self._name_is_corpse(container_name, c) for c in corpses):
                return self.search_corpse(container_name)
            if not container_visual or container_name.lower() not in container_visual.lower():
                return f"You don't see a {container_name} here to open."
        if not active_loot_source:
            if any(not c.get("searched") for c in corpses):
                return self.search_corpse()
            if container_visual:
                return f"The {container_visual} is empty."
            return "There are no containers here to open."

        def process_loot(loot_data):
            item = None
            gold = 0
            if isinstance(loot_data, dict):
                item = loot_data.get("item")
                gold = loot_data.get("gold", 0)
            elif isinstance(loot_data, str):
                item = loot_data
            if item:
                self.player.add_item(item)
            if gold > 0:
                self.player.update_gold(gold)
            parts = []
            if item:
                parts.append(f"a {item}")
            if gold > 0:
                parts.append(f"{gold} gold")
            return " and ".join(parts) if parts else "nothing"

        unlock_clause = ""
        if is_locked:
            inventory = self.player.data["inventory"]
            found_key = next((k for k in KEY_DATA if inventory.get(k, 0) > 0), None)
            if found_key:
                self.player.remove_item(found_key, 1)
                room["chest"]["locked"] = False
                self.world.save()
                unlock_clause = f"You turn the {found_key} (consumed) and open the"
            elif inventory.get("Lockpick", 0) > 0:
                floor_level = abs(int(pos["z"]))
                dc = 11 + floor_level
                self.player.update_satiety(-1)
                roll = self.roll_dice(20, self.get_stat_bonus("DEX"))
                if roll >= dc:
                    room["chest"]["locked"] = False
                    self.world.save()
                    unlock_clause = f"You coax the tumblers with your lockpick (roll {roll} vs DC {dc}) and open the"
                else:
                    snap = ""
                    if random.random() < 0.5:
                        self.player.remove_item("Lockpick", 1)
                        snap = " The lockpick snaps!"
                    return f"You fiddle with the lock (roll {roll} vs DC {dc}) but it won't give.{snap}"
            else:
                return f"The {container_visual or 'container'} is locked. You'll need a key or a lockpick."

        if room.get("trap"):
            trap_type = room.get("trap_type")
            if TRAP_DATA.get(trap_type, {}).get("location") == "Chest":
                res = self._resolve_trap(trap_type)
                if res["triggered"]:
                    loot_desc = process_loot(active_loot_source)
                    if key in self.world.rooms:
                        self.world.rooms[key]["loot"] = None
                        self.world.rooms[key]["loot_taken"] = True
                        self.world.save()
                    return (f"{res['msg']} However, inside the {container_visual or 'container'}, "
                            f"you find {loot_desc}!")
        loot_desc = process_loot(active_loot_source)
        if key in self.world.rooms:
            self.world.rooms[key]["loot"] = None
            self.world.rooms[key]["loot_taken"] = True
            self.world.save()
        if unlock_clause:
            return f"{unlock_clause} {container_visual or 'container'}! Inside you find {loot_desc}."
        return f"You open the {container_visual or 'container'} and find {loot_desc} inside!"
