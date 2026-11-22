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
LEDGER_DATA = {}
LEDGER = {}


def load_ledger():
    """(Re)load the items ledger JSON into the module-level category globals."""
    global REAGENT_DATA, WEAPON_DATA, RANGED_DATA, ARMOR_DATA, PROVISIONS_DATA, GENERAL_DATA
    global TRANSPORT_DATA, KEY_DATA, LOCKPICK_DATA, LEDGER_DATA, LEDGER, POUCH_DATA
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
                spell_name = random.choice(list(SPELL_DATA.keys()))
                item_to_give = (f"Recipe: {spell_name}" if random.random() > 0.4
                                else f"Partial Recipe: {spell_name}")
                item_price = 0
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
        "time": {"day": 1, "phase": 0, "tick": 0},
        "stats": {"STR": 11, "DEX": 11, "CON": 11, "INT": 11, "WIS": 11, "CHA": 11},
        "position": {"x": -1, "y": 1, "z": 0},
        "last_direction": None,
        "inventory": {"Cloth Robe": 1},
        "equipped": {"weapon": None, "armor": "Cloth Robe", "shield": None},
        "quest": {"active": None, "pending": None, "recent": [], "declined": []},
    }


class PlayerState:
    def __init__(self, game_engine=None):
        self.engine = game_engine
        self.data = default_player_data()
        self.load()
        self.refresh_derived_stats()

    def load(self):
        if os.path.exists(PLAYER_FILE):
            try:
                with open(PLAYER_FILE, "r") as f:
                    self.data.update(json.load(f))
            except json.JSONDecodeError:
                pass
        self.data.setdefault("time", {"day": 1, "phase": 0, "tick": 0})
        self.data.setdefault("item_ages", {})

    def save(self):
        with open(PLAYER_FILE, "w") as f:
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

    def update_hp(self, amount):
        self.data["hp"] = max(0, min(self.data["max_hp"], self.data["hp"] + int(amount)))
        self.save()
        if self.data["hp"] <= 0:
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
            slot, canonical = "weapon", LIGHT_FUEL_KEYS[key]
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
        self.death_message = None
        self.pending_food_msgs = []
        self.last_parley_verdict = None

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
        self._age_perishables(int(ticks))
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

    def _age_perishables(self, ticks):
        """Advance food spoilage with the clock; queue flavor for turned items."""
        if ticks <= 0:
            return
        inv = self.player.data["inventory"]
        ages = self.player.data.setdefault("item_ages", {})
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
            self.player.save()

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
        return self.player.data

    # --- scene / narration context (was an RpgModule tool) ---
    def get_scene_context(self):
        p = self.player.data
        pos = p["position"]
        room_id = f"{pos['x']}_{pos['y']}_{pos['z']}"
        self.world.engine_phase = self.player.data.setdefault(
            "time", {"day": 1, "phase": 0, "tick": 0})["phase"]
        room = self.world.get_room(pos["x"], pos["y"], pos["z"])
        npc = NPC_REGISTRY.get(room_id)
        monster = room.get("encounter", "None")
        is_dark = (not self.room_is_lit(room)) and not self.player.is_lit()

        if is_dark:
            description = DARKNESS_DESCRIPTION
            visual_elements = "Absolute darkness"
            npc_presence = ("You hear something stirring in the dark..."
                            if (npc or monster != "None") else "The silence is absolute.")
            # In the dark the player cannot see the room's objects, so we do not
            # claim a chest/trap is visible - only what a light would reveal.
            room_contents = "unseen in the darkness (light a torch and look)"
        else:
            description = room.get("description")
            if not description:
                description = ROOM_TYPE_DESCRIPTIONS.get(room.get("type", "Unknown"), "A dimly lit area.")
            # A cleared Combat room (no live monster) should not keep claiming
            # something lurks - the 'Combat' type is stale once its threat is
            # dead. Any corpse left behind is reported separately in PRESENT.
            if room.get("type") == "Combat" and monster == "None":
                description = "A quiet chamber - the danger here has passed."
            if room.get("type") == "Discovery" and room.get("discovery"):
                description = DISCOVERY_DATA.get(room["discovery"], {}).get(
                    "flavor", description)
            elif room.get("atmosphere"):
                description = f"{description} {room['atmosphere']}"
            visual_elements = ", ".join(room.get("visual_tags", [])) if room.get("visual_tags") else "none"
            if npc:
                npc_presence = f"NPC Present: {npc.name}"
            elif monster != "None":
                npc_presence = f"Monster Present: {monster}"
            else:
                npc_presence = "No NPCs or monsters present."
            corpse = room.get("corpse")
            if corpse:
                state = ("picked clean" if corpse.get("searched")
                         else "unsearched - try 'search corpse'")
                npc_presence += (f" | The corpse of the {corpse.get('monster', 'creature')} "
                                 f"lies here ({state}).")
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
        monster = current_room.get("encounter", "None")
        p_data = self.player.data
        eq = p_data["equipped"]
        fuel = p_data["light_fuel"]

        if eq.get("weapon") == "Torch" and fuel["torch"] > 0:
            fuel["torch"] -= 1
            if fuel["torch"] <= 0:
                eq["weapon"] = None
        if "Lantern" in eq.values() and fuel["lantern"] > 0:
            fuel["lantern"] -= 1
            if fuel["lantern"] <= 0:
                for slot, item in eq.items():
                    if item == "Lantern":
                        eq[slot] = None
        if p_data["light_duration"] > 0:
            p_data["light_duration"] -= 1
        self.player.save()

        if monster != "None":
            opposites = {"North": "South", "South": "North", "East": "West", "West": "East",
                         "Up": "Down", "Down": "Up"}
            last_dir = self.player.data.get("last_direction")
            attack_res = self.resolve_monster_attack(monster)
            if attack_res["hit"]:
                self.player.update_hp(-attack_res["damage"])
            if last_dir and direction == opposites.get(last_dir):
                res = self._execute_move(direction)
                if "error" not in res:
                    res["combat_event"] = "escaped"
                    res["monster_attack"] = attack_res
                return res
            else:
                return {"error": "The monster blocks your path!",
                        "combat_event": "blocked", "monster_attack": attack_res,
                        "position": current_pos, "room": current_room}

        res = self._execute_move(direction)
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
        return res

    def _resolve_trap(self, trap_type):
        trap_info = TRAP_DATA.get(trap_type, TRAP_DATA["Pressure Plate (Darts)"])
        save_roll = self.roll_dice(20, self.get_stat_bonus("DEX"))
        if save_roll < trap_info["dc"]:
            damage = trap_info["damage"] + (abs(self.player.data["position"]["z"]) // 2)
            self.player.update_hp(-damage)
            return {"triggered": True, "damage": damage,
                    "msg": f"The {trap_type} triggered! You take {damage} damage."}
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

        if direction in current_room.get("doors", {}):
            door = current_room["doors"][direction]
            if door.get("state") == "locked":
                return {"error": f"The door to the {direction} is locked. "
                                 "('open to try - a key or lockpick may serve.')",
                        "position": current_pos, "room": current_room}
            return {"error": f"A closed door stands to the {direction}. Try 'open {direction.lower()}'.",
                    "position": current_pos, "room": current_room}
        elif direction in current_room.get("obstacles", {}):
            ob = current_room["obstacles"][direction]
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

        self.player.data["position"] = {"x": tx, "y": ty, "z": tz}
        self.player.data["last_direction"] = direction
        self.player.save()
        room = self.world.get_room(tx, ty, tz)
        return {"position": self.player.data["position"], "room": room,
                "satiety": self.player.data["satiety"],
                "wandering_check": (self.turn_count % 10 == 0), "lore_grounding": NARRATIVE_CONTEXT}

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
        # fall back to the shop registered on the NPC standing here.
        npc = NPC_REGISTRY.get(room_id)
        if npc and getattr(npc, "shop", None) and npc.shop in inventories:
            return npc.shop, inventories[npc.shop]
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

    def get_stat_bonus(self, stat_name):
        return self.player.data["stats"].get(stat_name, 10) - 10

    def _find_monster(self, monster_name):
        bestiary = self.world.generator.bestiary
        if not monster_name:
            return None
        if monster_name in bestiary:
            return monster_name

        def get_words(s):
            return set(re.findall(r"\w+", s.lower()))

        search_words = get_words(monster_name)
        for key in bestiary:
            if search_words == get_words(key):
                return key
        return None

    DARK_ROOM_TAGS = ("oppressive darkness", "darkness")

    def room_is_lit(self, room=None):
        """True if the room itself provides light (town, torch-lit dungeon).
        Mirrors the combat darkness rule: only 'oppressive darkness' rooms are dark."""
        if room is None:
            pos = self.player.data["position"]
            room = self.world.get_room(pos["x"], pos["y"], pos["z"])
        tags = room.get("visual_tags") or []
        return not any(t in self.DARK_ROOM_TAGS for t in tags)

    def calculate_ac(self):
        armor_bonus = (ARMOR_DATA[self.player.data["equipped"]["armor"]]["ac"]
                       if self.player.data["equipped"]["armor"] in ARMOR_DATA else 0)
        shield_bonus = (ARMOR_DATA[self.player.data["equipped"]["shield"]]["ac"]
                        if self.player.data["equipped"]["shield"] in ARMOR_DATA else 0)
        dex = self.player.data["stats"].get("DEX", 10)
        return armor_bonus + shield_bonus + dex

    def calculate_attack_bonus(self, weapon_name):
        weapon_stats = WEAPON_DATA.get(weapon_name, {})
        w_type = weapon_stats.get("type", "melee")
        w_mod = weapon_stats.get("mod", 0)
        return (self.get_stat_bonus("DEX") if w_type == "ranged" else self.get_stat_bonus("STR")) + w_mod

    def resolve_attack(self, attacker_weapon, defender_ac, penalty=0):
        attack_roll = self.roll_dice(20, self.calculate_attack_bonus(attacker_weapon)) + penalty
        if attack_roll >= defender_ac:
            weapon_stats = WEAPON_DATA.get(attacker_weapon, {"die": 6, "mod": 0})
            damage = random.randint(1, weapon_stats["die"]) + self.get_stat_bonus("STR") + weapon_stats.get("mod", 0)
            return {"hit": True, "damage": max(1, damage), "roll": attack_roll}
        return {"hit": False, "damage": 0, "roll": attack_roll}

    def _roll_poison_save(self, dc=10):
        con_bonus = self.get_stat_bonus("CON")
        roll = self.roll_dice(20, con_bonus)
        success = roll >= dc
        already_poisoned = "Poisoned" in self.player.data.get("status_effects", [])
        applied = False
        refreshed = False
        if not success:
            if not already_poisoned:
                self.player.data.setdefault("status_effects", []).append("Poisoned")
                self.player.data["poison_duration"] = 3
                applied = True
            else:
                self.player.data["poison_duration"] = max(int(self.player.data.get("poison_duration", 0)), 2)
                refreshed = True
        self.player.save()
        return {"roll": roll, "bonus": con_bonus, "dc": dc, "success": success,
                "applied": applied, "refreshed": refreshed, "already_poisoned": already_poisoned}

    def resolve_monster_attack(self, monster_name):
        monster_id = self._find_monster(monster_name)
        monster = self.world.generator.bestiary.get(monster_id, {})
        if not monster:
            return {"hit": False, "damage": 0, "message": "The creature is confused and misses!"}
        player_ac = self.calculate_ac()
        attack_bonus = monster.get("atk", monster.get("attack_bonus", 0))
        roll = random.randint(1, 20) + attack_bonus
        if roll >= player_ac:
            dmg_die = monster.get("damage_die", 6)
            dmg_mod = monster.get("damage_mod", 0)
            damage = max(1, random.randint(1, dmg_die) + dmg_mod)
            result = {"hit": True, "damage": damage, "roll": roll,
                      "message": f"The {monster_name} hits you for {damage} damage."}
            if "poison" in monster.get("special", []):
                poison_save = self._roll_poison_save(dc=monster.get("poison_dc", 10))
                result["poison_save"] = poison_save
                if poison_save["applied"] and not poison_save["already_poisoned"]:
                    result["message"] += (" You feel venom sink into your wound. You fail your CON save "
                                          f"({poison_save['roll']} vs DC {poison_save['dc']}) and are poisoned.")
                elif poison_save["applied"] or poison_save["refreshed"]:
                    result["message"] += (" Fresh venom wracks you. You fail your CON save "
                                          f"({poison_save['roll']} vs DC {poison_save['dc']}) and your poison lingers.")
                else:
                    result["message"] += (" You feel venom sink into your wound, but you pass your CON save "
                                          f"({poison_save['roll']} vs DC {poison_save['dc']}).")
            return result
        return {"hit": False, "damage": 0, "roll": roll, "message": f"The {monster_name} misses."}

    def _clear_poison(self):
        self.player.data["poison_duration"] = 0
        self.player.data["status_effects"] = [
            e for e in self.player.data.get("status_effects", []) if e != "Poisoned"
        ]
        self.player.save()

    def _tick_poison(self):
        if "Poisoned" not in self.player.data.get("status_effects", []):
            return None
        damage = 1
        hp_result = self.player.update_hp(-damage)
        died = isinstance(hp_result, str)
        if died:
            self._clear_poison()
            return {"damage": damage, "remaining": 0, "player_died": True,
                    "death_message": hp_result, "message": "The poison finishes you."}
        duration = int(self.player.data.get("poison_duration", 1)) - 1
        if duration <= 0:
            self._clear_poison()
            return {"damage": damage, "remaining": 0, "player_died": False,
                    "message": "The poison burns through you and fades. (-1 HP)"}
        self.player.data["poison_duration"] = duration
        self.player.save()
        return {"damage": damage, "remaining": duration, "player_died": False,
                "message": f"The poison courses through you. (-1 HP, {duration} round(s) left)"}

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
            "Sleep": lambda: "A heavy mist descends. (Targets fall asleep - handled by AI)",
            "Detect Magic": lambda: self.detect_magic(),
            "Purify Food": lambda: self._purify_food(),
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
        return {"success": False, "message": "Meditation failed."}

    def sleep(self):
        pos = self.player.data["position"]
        z = pos["z"]
        satiety_cost = 15
        if z == 0:
            if pos["x"] == -1 and pos["y"] == -1:
                gold_cost = 1
                if self.player.data["gold"] < gold_cost:
                    return {"success": False, "message": "You don't have enough gold for a room at the inn."}
                if self.player.data["satiety"] < satiety_cost:
                    return {"success": False, "message": "You are too hungry to sleep properly; you need more food."}
                self.player.update_gold(-gold_cost)
                self.player.data["satiety"] = max(0, self.player.data["satiety"] - satiety_cost)
                self.player.update_hp(self.player.data["max_hp"] - self.player.data["hp"])
                self._bump_virtue_day()
                self.sleep_until_morning()
                label = self.time_label()
                return {"success": True, "message": "You spend a restful night at the Gilded Flagon Inn. You wake up fully refreshed."
                        + (f" It is {label}." if label else "")}
            else:
                return {"success": False, "message": "You are in town, but not at the inn. You can't just sleep on the street!"}
        else:
            if "Bedroll" not in self.player.data["inventory"]:
                return {"success": False, "message": "You don't have a bedroll. Sleeping on the cold dungeon floor is a recipe for disaster"}
            if self.player.data["satiety"] < satiety_cost:
                return {"success": False, "message": "You are too hungry to sleep properly; you need more food."}
            if z < 0:
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

    def _clear_room_encounter(self, room_id):
        """Remove the encounter from a room (dungeon or town) and persist."""
        cleared = False
        if room_id in self.world.rooms:
            self.world.rooms[room_id]["encounter"] = "None"
            self.world.save(); cleared = True
        if room_id in self.world.town_rooms:
            self.world.town_rooms[room_id]["encounter"] = "None"
            with open(os.path.join(BASE_DIR, "data", "town_map.json"), "w") as f:
                json.dump(self.world.town_rooms, f, indent=1)
            cleared = True
        return cleared

    def resolve_combat_turn(self, monster_name, player_action, player_weapon, monster_action, formula=None):
        room_id = f"{self.player.data['position']['x']}_{self.player.data['position']['y']}_{self.player.data['position']['z']}"
        room = self.world.get_room(self.player.data["position"]["x"],
                                   self.player.data["position"]["y"],
                                   self.player.data["position"]["z"])
        is_dark = (not self.room_is_lit(room)) and not self.player.is_lit()
        attack_penalty = -4 if is_dark else 0
        monster_id = self._find_monster(monster_name)
        bestiary = self.world.generator.bestiary
        if not monster_id:
            return {"error": "Monster not found."}
        if room_id not in self.combat_state:
            self.combat_state[room_id] = bestiary[monster_id].get("hp", 10)
        m_hp = self.combat_state[room_id]
        p_hp = self.player.data["hp"]
        p_res = {"hit": False, "damage": 0, "message": ""}
        if player_action == "fight":
            p_res = self.resolve_attack(player_weapon, bestiary[monster_id]["ac"], penalty=attack_penalty)
            if p_res["hit"]:
                self.combat_state[room_id] -= p_res["damage"]
        elif player_action == "mix" and formula:
            spell_res = self.mix_reagents(formula)
            p_res["message"] = spell_res.get("message", "The mixture fizzles.")
            p_res["spell"] = spell_res.get("spell")
            p_res["auras"] = spell_res.get("auras")
        poison_tick = self._tick_poison()
        m_res = {"hit": False, "damage": 0, "message": ""}
        if monster_action == "fight":
            m_res = self.resolve_monster_attack(monster_name)
            if m_res["hit"]:
                self.player.update_hp(-m_res["damage"])
        elif monster_action == "special":
            special_msg = self.apply_monster_special(monster_name)
            m_res["message"] = special_msg or "The monster bellows menacingly!"
        elif monster_action == "flee":
            m_res["message"] = "The monster turns and flees into the darkness!"
            self._clear_room_encounter(room_id)
        monster_dead = self.combat_state[room_id] <= 0
        player_dead = self.player.data["hp"] <= 0
        virtue_msg = None
        drop_lines = None
        if monster_dead:
            self._clear_room_encounter(room_id)
            self.player.update_virtue(2)
            # The spoils are stowed inside the corpse - nothing is granted
            # until the player searches it.
            drop_lines = [self._deposit_corpse(
                room_id, monster_name, self.roll_monster_drops(monster_name))]
            if player_action == "fight" and p_hp <= self.player.data["max_hp"] // 2:
                led = self._virtue_ledger()
                led["last_stand"] = led.get("last_stand", 0) + 1
                self.player.save()
                virtue_msg = " Last stand logged - Raika will hear of it."
        return {"player_attack": p_res, "monster_attack": m_res, "poison_tick": poison_tick,
                "monster_hp": self.combat_state[room_id], "monster_dead": monster_dead,
                "player_dead": player_dead, "virtue_msg": virtue_msg,
                "drop_lines": drop_lines}

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
            self._clear_room_encounter(room_id)
            self.combat_state.pop(room_id, None)
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

    def talk_to_npc(self):
        pos = self.player.data["position"]
        room_id = f"{pos['x']}_{pos['y']}_{pos['z']}"
        npc = NPC_REGISTRY.get(room_id)
        if not npc:
            return {"error": "There is no one here to talk to."}
        knowledge = npc.hints.copy()
        if npc.name == "Xylos the Archivist":
            low_level_recipes = {name: info["reagents"] for name, info in SPELL_DATA.items() if info["level"] <= 2}
            knowledge["Common Spell Recipes"] = low_level_recipes
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

    def _deposit_corpse(self, room_id, monster_name, loot):
        """Leave a searchable corpse holding the kill's loot in the room.
        Returns a one-line flavour message for the combat report."""
        for store in (self.world.rooms, getattr(self.world, "town_rooms", {})):
            room = store.get(room_id)
            if room is not None:
                room["corpse"] = {"monster": monster_name, "loot": loot,
                                  "searched": False}
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
        """Loot the corpse left by a slain monster in the current room: takes
        its gold and items, then marks it picked clean."""
        pos = self.player.data["position"]
        room_id = f"{pos['x']}_{pos['y']}_{pos['z']}"
        room = self._authoritative_room(room_id)
        corpse = (room or {}).get("corpse")
        if not corpse:
            return "There is no corpse here to search."
        if not self._name_is_corpse(name, corpse):
            return f"There is no {name} here - only the corpse of the {corpse.get('monster', 'it')}."
        if corpse.get("searched"):
            return f"The corpse of the {corpse.get('monster', 'it')} has already been picked clean."
        loot = corpse.get("loot") or {}
        gold = int(loot.get("gold", 0) or 0)
        items = [list(e) for e in (loot.get("items", []) or [])]
        taken = []
        notes = []
        leftovers = []
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
        # Whatever would not fit stays behind, so a fuller pack can come back
        # for it; the corpse is only picked clean once it is empty.
        corpse["loot"] = {"gold": 0, "items": leftovers}
        corpse["searched"] = not leftovers
        self._save_room(room_id)
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
        return " ".join([head] + notes)

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
                res = self.player.add_item(f"Recipe: {spell}")
                ok = not (isinstance(res, dict) and not res.get("success"))
                self.player.update_virtue(1)
                return (f"{flavor} At the circle's center, a page lies folded: a spell "
                        f"recipe for {spell}." + ("" if ok else " (You cannot carry it - it flutters back down.)"))
            if roll == 2:
                spell = random.choice(list(SPELL_DATA.keys()))
                res = self.player.add_item(f"Partial Recipe: {spell}")
                ok = not (isinstance(res, dict) and not res.get("success"))
                return (f"{flavor} Half a recipe, inked on one corner of the circle: "
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

            monster = room.get("encounter", "None")
            if monster and monster != "None":
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

            corpse = room.get("corpse")
            if corpse and not corpse.get("searched"):
                c_loot = corpse.get("loot") or {}
                if int(c_loot.get("gold", 0) or 0) > 0 or (c_loot.get("items") or []):
                    auras.append({"source": f"the corpse of the {corpse.get('monster', 'creature')}",
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

    def interact_environment(self, action):
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
        if any(w in action_lower for w in ("rubble", "force", "push", "pull", "break", "crowbar", "portcullis")):
            stat = "STR"
        elif any(w in action_lower for w in ("jump", "leap", "balance", "squeeze", "climb", "gap")):
            stat = "DEX"
        elif any(w in action_lower for w in ("magic", "spell", "reagent", "chant", "ward", "barrier", "elemental")):
            stat = "INT"
        else:
            stat = "WIS"
        roll = self.roll_dice(20, self.get_stat_bonus(stat))
        dc = 12
        if roll >= dc:
            return (f"Check: {action} using {stat} against DC {dc} succeeded ({roll}). "
                    "The obstacle has not been cleared automatically; call clear_obstacle() if the solution should finish the puzzle.")
        return (f"Check: {action} using {stat} against DC {dc} failed ({roll}). The obstacle still blocks the way.")

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
                return (f"The {ob.get('name', 'obstacle')} bars the way to the {direction} - "
                        "no hinge, no handle. Try 'force' (or improvise).")
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

    def force_way(self, direction_word=None, action=""):
        """'force <dir>' - throw yourself at an obstacle (or a stuck door).
        The attempt costs an hour of effort; success clears the way for good.
        Always retryable - a failure is time, never a tomb."""
        pos = self.player.data["position"]
        rid = f"{pos['x']}_{pos['y']}_{pos['z']}"
        room = self.world.rooms.get(rid)
        if room is None:
            return "Nothing to force here - you are in town."
        direction = self._resolve_direction(direction_word)
        if not direction:
            return "Which way? (north, south, east, west)"
        pair = self._edge_rooms(rid, direction)
        if pair is None:
            return f"No way to the {direction} to force."
        ob = room.get("obstacles", {}).get(direction)
        if ob is None:
            if direction in room.get("doors", {}):
                return (f"The door to the {direction} answers to 'open', not to force. "
                        "(Unless you fancy the hinges.)")
            return f"The way to the {direction} is already open."
        name = ob.get("name", "obstacle")
        desc = OBSTACLE_DATA.get(name, "An obstacle blocks the way.")
        low = str(action).lower()
        if any(w in low for w in ("rubble", "shove", "push", "pull", "break", "crowbar",
                                  "boulder", "muscle", "shoulder")):
            stat = "STR"
        elif any(w in low for w in ("jump", "leap", "balance", "squeeze", "climb", "gap",
                                    "swing", "walk")):
            stat = "DEX"
        elif any(w in low for w in ("magic", "spell", "reagent", "chant", "ward", "barrier",
                                    "elemental", "glyph", "knock")):
            stat = "INT"
        else:
            stat = "WIS"
        self.player.update_satiety(-1)
        roll = self.roll_dice(20, self.get_stat_bonus(stat))
        dc = 12
        if roll >= dc:
            self._promote_edge(pair, direction)
            self.world.save()
            return (f"You set yourself against the {name} ({stat} roll {roll} vs DC {dc}). "
                    f"It resists once - then gives. The way to the {direction} is open.")
        return (f"You set yourself against the {name} ({stat} roll {roll} vs DC {dc}) "
                f"and it does not care. {desc}")

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
        corpse = room.get("corpse")
        if container_name:
            if self._resolve_direction(container_name):
                return self.open_door(container_name)
            if corpse and self._name_is_corpse(container_name, corpse):
                return self.search_corpse(container_name)
            if not container_visual or container_name.lower() not in container_visual.lower():
                return f"You don't see a {container_name} here to open."
        if not active_loot_source:
            if corpse and not corpse.get("searched"):
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
