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

"""tools.py - tool schemas + dispatch for the improvise sub-loop.

The LLM may only call these; every one maps to an existing GameEngine method,
so the engine stays authoritative over game state.
"""

import json

SCHEMAS = [
    {"type": "function", "function": {
        "name": "get_scene_context",
        "description": "Return the current room name, description, visual tags, and who/what is present.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "talk_to_npc",
        "description": "Interview the NPC present in the room; returns name, description, traits and knowledge hints.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "search_traps",
        "description": "Roll to notice a hidden trap in the current room.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "disarm_trap",
        "description": "Attempt to disarm the trap in the current room (DEX check; fumble deals damage).",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "interact_environment",
        "description": "Try a freeform interaction with the puzzle obstacle (e.g. 'push the rubble', 'chant the ward'). Rolls an ability check.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "description": "The physical or magical thing attempted."}},
            "required": ["action"]}}},
    {"type": "function", "function": {
        "name": "clear_obstacle",
        "description": "Declare the puzzle obstacle solved and remove it (only after a successful interact_environment).",
        "parameters": {"type": "object", "properties": {
            "obstacle_name": {"type": "string", "description": "Optional name to verify."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "open_container",
        "description": "Open the chest/container in the current room and roll its loot. A slain monster's corpse can be opened this way too.",
        "parameters": {"type": "object", "properties": {
            "container_name": {"type": "string"}}, "required": []}}},
    {"type": "function", "function": {
        "name": "search_corpse",
        "description": "Search the corpse left by a slain monster in the current room and take the gold and items inside it (the corpse is the loot container - nothing is granted until you search it).",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Optional: 'corpse', 'body', or the monster's name."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "discard_item",
        "description": "Throw an item (or some of a stack) out of the pack for good. Omit quantity to discard the whole stack. A discarded stack that empties also clears any equipped slot wearing that item.",
        "parameters": {"type": "object", "properties": {
            "item": {"type": "string", "description": "The item to discard (forgiving name)."},
            "quantity": {"type": "integer", "description": "Optional: how many to discard (default: the whole stack)."}},
            "required": ["item"]}}},
    {"type": "function", "function": {
        "name": "scan_magic",
        "description": "Read the room as if Detect Magic were cast: returns the list of magical auras (source, intensity, kind, optional hint). Free to call - no reagents consumed.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "roll_dice",
        "description": "Roll arbitrary dice for flavour checks, e.g. sides=20 modifier=3.",
        "parameters": {"type": "object", "properties": {
            "sides": {"type": "integer"}, "modifier": {"type": "integer"}},
            "required": ["sides"]}}},
]

SCHEMA_NAMES = {s["function"]["name"] for s in SCHEMAS}

# --- combat-scoped schemas (parley sub-loop) --------------------------------
# Only the tools a mid-fight roleplay attempt needs; the engine still decides
# every mechanical outcome.
COMBAT_SCHEMAS = [
    s for s in SCHEMAS if s["function"]["name"] in ("get_scene_context", "roll_dice")
] + [
    {"type": "function", "function": {
        "name": "resolve_parley",
        "description": ("Attempt to end the fight without a blow: talk, bargain, "
                        "bribe, intimidate, charm or hide from the monster. Call "
                        "this EXACTLY ONCE. The engine rolls the die and returns "
                        "the verdict as JSON (pacified true/false, roll, dc, "
                        "gold_spent, monster_attack)."),
        "parameters": {"type": "object", "properties": {
            "approach": {"type": "string", "description":
                "What the player is trying: negotiate / bargain / bribe / "
                "intimidate / charm / plead / hide / sneak ..."},
            "gold_offered": {"type": "integer", "description":
                "Gold coins offered as a bribe or payment (0 if none)."}},
            "required": ["approach"]}}},
]


def dispatch(engine):
    """Return a callable(name, args) -> str bound to a live GameEngine."""
    def run(name, args):
        args = args or {}
        try:
            if name == "get_scene_context":
                ctx = engine.get_scene_context()
                return json.dumps({k: v for k, v in ctx.items() if k != "lore_grounding"})
            if name == "talk_to_npc":
                return json.dumps(engine.talk_to_npc())
            if name == "search_traps":
                return str(engine.search_traps())
            if name == "disarm_trap":
                return str(engine.disarm_trap())
            if name == "interact_environment":
                return str(engine.interact_environment(args.get("action", "examine")))
            if name == "clear_obstacle":
                return str(engine.clear_obstacle(args.get("obstacle_name")))
            if name == "open_container":
                return str(engine.open_container(args.get("container_name")))
            if name == "search_corpse":
                return str(engine.search_corpse(args.get("name")))
            if name == "discard_item":
                qty = args.get("quantity")
                res = engine.player.discard_item(args.get("item"), qty)
                return json.dumps({k: res.get(k) for k in ("success", "item", "discarded", "have", "message") if k in res})
            if name == "scan_magic":
                res = engine.scan_magic()
                return json.dumps({"room": res["room"], "in_town": res["in_town"],
                                   "auras": res["auras"], "reading": res["reading"]})
            if name == "roll_dice":
                return str(engine.roll_dice(args.get("sides", 20), args.get("modifier", 0)))
            if name == "resolve_parley":
                res = engine.resolve_parley(args.get("monster"),
                                            args.get("approach", "negotiate"),
                                            args.get("gold_offered", 0))
                return json.dumps(res)
            return f"Unknown tool '{name}'. Available: {', '.join(sorted(SCHEMA_NAMES))}"
        except Exception as exc:  # engine errors must never crash the loop
            return f"Engine error in {name}: {exc}"
    return run
