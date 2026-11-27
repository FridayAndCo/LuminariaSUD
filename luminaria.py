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
"""luminaria.py - terminal RPG (port of the rpgmodule webui app).

Architecture: engine-driven 95% of turns (deterministic resolution + one
LLM prose call with no tools); puzzle/weird rooms can escalate into a
bounded 'improvise' sub-loop where the LLM may call engine tools.
"""

import json
import os
import random
import re
import sys

import engine as E
import tools as T
import ui
from llm import LLMClient

BASE = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(BASE, "config.json")

DEFAULT_CFG = {
    "api_url": "http://192.168.1.58:5001/v1",
    "model": "koboldcpp/Qwen3.8-Flash-Next-UD-Q4_K_XL-00001-of-00004",
    "api_key": "none",
    "narrate": True,
    "insight_budget": 3,
    "max_improvise_steps": 5,
    "temperature": 0.8,
    "max_tokens": 300,
    "disable_thinking": True,
    "improvise_room_types": ["Puzzle"],
}

SYSTEM_NARRATOR = (
    "You are the narrator of a dark-fantasy terminal RPG set in Luminaria, "
    "a city-dungeon where memories drift like fog. The player has just taken "
    "an action whose OUTCOME IS ALREADY DECIDED by the game engine. "
    "Restyle the outcome as vivid but CONCISE prose (1-3 sentences), second "
    "person, present tense. The SCENE line is only a mood hint; the PRESENT and "
    "CONTENTS lines are the authoritative, up-to-date facts about what is really "
    "here - trust them over the SCENE. If CONTENTS lists a chest, trap or shrine, "
    "the room is NOT empty and you must mention it; if nothing lurks in PRESENT, "
    "do not say something lurks. Do NOT invent new mechanical facts, damage, "
    "items, or movement. Do not add chain-of-thought. Output only the narration."
)

SYSTEM_IMPROV = (
    "You are the rules-lawyer game master of Luminaria, a dark-fantasy "
    "dungeon-crawl. The player is facing a strange/puzzle situation. You may "
    "call engine tools to inspect or resolve it, but the ENGINE is "
    "authoritative - never invent mechanical results. Use tools if you need "
    "information; when the moment is resolved, reply in 1-3 sentences of "
    "second-person prose describing what happened. Do not call more than a "
    "few tools. When you resolve the player's idea with interact_environment, "
    "name the single most relevant stat (STR/DEX/CON/INT/WIS/CHA) and assign "
    "an idea_bonus from -5 (a terrible idea) to +5 (a great idea) based on how "
    "sensible the attempt is - a torch on a wall of thorns is a great idea, "
    "lightning bolt underwater is a terrible one. You judge the idea; the "
    "engine rolls the die."
)

SYSTEM_COMBAT_IMPROV = (
    "You are the rules-lawyer game master of COMBAT in Luminaria, a "
    "dark-fantasy dungeon-crawl. Mid-fight, the player is trying to ROLEPLAY "
    "their way out: talk, bargain, bribe, intimidate, charm, plead or hide "
    "from the monster. Call the tool resolve_parley EXACTLY ONCE with the "
    "best single-word 'approach' for their attempt and 'gold_offered' (use "
    "the GOLD OFFERED figure from the prompt). The ENGINE rolls the die and "
    "returns the verdict - never invent the outcome yourself. You may call "
    "get_scene_context first if you need to see the room. Then reply in 1-3 "
    "sentences of second-person prose: if pacified, the monster stands down "
    "and withdraws (it is NOT slain - it leaves no corpse or loot); if the "
    "attempt fails, the monster gets a free blow. Do not add chain-of-thought."
)

SYSTEM_CONV = (
    "You are roleplaying a single NPC in a dark-fantasy terminal RPG set in "
    "Luminaria, a city-dungeon where memories drift like fog. Stay strictly "
    "in character using the NPC sheet provided (name, description, traits, "
    "knowledge). Reply to the player in 1-3 sentences. You may hint at your "
    "knowledge but never invent major mechanical facts. When the player asks "
    "to leave, put the single word FAREWELL at the end of your reply. Use "
    "second person for the player."
)


SYSTEM_DETECT = (
    "You are the arcane-sense narrator of a dark-fantasy terminal RPG set in "
    "Luminaria, a city-dungeon where memories drift like fog. The player has "
    "cast DETECT MAGIC. The ENGINE hands you the auras it found in this room as "
    "a JSON list: each has a 'source', an 'intensity' (mundane, faint, steady, "
    "strong or radiant) and a 'kind' describing what the magic IS, plus an "
    "optional 'hint' about how it might be overcome. "
    "Turn that into 1-3 sentences of second-person prose that the player can "
    "act on: say WHERE the magic is, HOW strong it reads, and WHAT it is doing. "
    "You may dramatise the sensation (heat, colour, sound, taste, pressure "
    "behind the eyes) but you must NOT invent new magical sources, new items, "
    "new mechanics, or a solution the engine did not hint at. If the reading is "
    "'mundane', say plainly that the thing is not magical - that is useful "
    "information, not a failure. If there are no auras at all, say the room "
    "reads as dead/ordinary. Output only the prose, no chain of thought."
)

SYSTEM_COMMUNE = (
    "You are roleplaying a DEAD creature in a dark-fantasy terminal RPG set in "
    "Luminaria, a city-dungeon where memories drift like fog. The player has "
    "cast COMMUNE WITH THE DEAD and asks up to three questions. You answer ONLY "
    "yes or no, in character, as the creature you were. A beast or a simple "
    "creature knows little of the wider dungeon - do NOT grant it knowledge it "
    "could not have had; a plain 'no' or a limited, confused answer is truthful "
    "and in-character. The room facts in the sheet are what is really here - "
    "trust them and answer honestly. Keep each answer to one short line. When "
    "the player asks to leave, put the single word FAREWELL at the end of your "
    "reply. Use second person for the player."
)

SYSTEM_SPEAK = (
    "You are roleplaying a single creature (an animal, or the plants in a room) "
    "in a dark-fantasy terminal RPG set in Luminaria, a city-dungeon where "
    "memories drift like fog. The player has cast SPEAK WITH ANIMALS or SPEAK "
    "WITH PLANTS and talks to you. Stay strictly in character using the sheet "
    "provided. Answer AS THE CREATURE YOU ARE: an animal knows the immediate "
    "area (what it has seen, smelled, heard) but little of the wider dungeon; "
    "the plants hold a secret the engine has already decided - reveal only that "
    "secret, do not invent new ones. Be truthful; the room facts in the sheet "
    "are what is really here. Keep replies to 1-3 short lines. When the player "
    "asks to leave, put the single word FAREWELL at the end of your reply. Use "
    "second person for the player."
)

SYSTEM_WISH = (
    "You are the rules-lawyer game master of Luminaria, a dark-fantasy "
    "dungeon-crawl. The player has cast WISH and described an effect. You may "
    "call engine tools to inspect or resolve it, but the ENGINE is "
    "authoritative - never invent mechanical results. Use the tools that best "
    "fulfil the wish as closely as the game's rules allow; if the wish cannot "
    "be granted within the rules, say so plainly in 1-3 sentences. When the "
    "moment is resolved, reply in 1-3 sentences of second-person prose "
    "describing what happened. Do not call more than a few tools."
)


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    if os.path.exists(CFG_FILE):
        try:
            with open(CFG_FILE) as f:
                cfg.update(json.load(f))
        except json.JSONDecodeError:
            pass
    return cfg


def llm_configured(cfg):
    """True when config.json points at a real endpoint (not the xxx placeholder)."""
    url = (cfg.get("api_url") or "").strip()
    return bool(url) and "xxx" not in url.lower()


class Game:
    def __init__(self, cfg):
        self.cfg = cfg
        E.load_ledger()
        self.eng = E.GameEngine()
        self.llm = LLMClient(cfg["api_url"], cfg["model"], cfg.get("api_key", "none"),
                             temperature=cfg.get("temperature", 0.8),
                             max_tokens=cfg.get("max_tokens", 300),
                             disable_thinking=cfg.get("disable_thinking", True))
        self.narrate_on = bool(cfg.get("narrate", True))
        self.llm_ready = llm_configured(cfg)
        wis = self.eng.player.data["stats"]["WIS"]
        self.insight_max = max(1, int(cfg.get("insight_base", 5)) + max(0, wis - 10))
        self.insight = self.insight_max
        self.dispatch = T.dispatch(self.eng)
        self.last_narration = None

    # ---------- narration ----------
    def narrate(self, event_text):
        if not self.narrate_on or not self.llm_ready or not event_text:
            return event_text
        ctx = self.eng.get_scene_context()
        user = (f"LOCATION: {ctx['room_name']}\n"
                f"SCENE: {ctx['description'][:300]}\n"
                f"PRESENT: {ctx['npc_presence']}\n"
                f"CONTENTS: {ctx.get('room_contents', 'nothing of note')}\n"
                f"ENGINE OUTCOME: {event_text}")
        text, err = self.llm.narrate(SYSTEM_NARRATOR, user)
        if err or not text:
            return event_text
        self.last_narration = text
        return text

    def narration_notice(self):
        """Startup one-liner about the narration/improvise situation."""
        if not self.llm_ready:
            return ("No LLM endpoint configured (config.json: api_url) - LLM "
                    "narration and improvise are unavailable. The engine plays "
                    "on: movement, combat, loot and quests all work.")
        if not self.narrate_on:
            return "LLM narration OFF (type 'narrate' to turn it on)."
        return "LLM narration ON (type 'narrate' to turn it off)."

    # ---------- improvise ----------
    def should_improvise(self):
        room = self._room()
        return bool(room.get("obstacles"))

    def _room(self):
        p = self.eng.player.data["position"]
        return self.eng.world.get_room(p["x"], p["y"], p["z"])

    def _respawn(self):
        """Death penalty & recovery: the High Priestess drags your soul back to
        the Temple of Radiance, and the wipe of death leaves you fully restored
        of mind. Insight returns to full on respawn."""
        self.insight = self.insight_max

    def _confirm(self, prompt):
        """Y/N gate before spending an insight charge. Default = yes."""
        try:
            ans = ui.prompt(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return True
        return ans in ("", "y", "yes")

    def _confirm_destructive(self, prompt):
        """Y/N gate for run-ending actions. Default = NO (must type y)."""
        try:
            ans = ui.prompt(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        return ans in ("y", "yes")

    def improvise(self, focus=None, confirm=True):
        if not self.llm_ready:
            ui.warn("No LLM endpoint configured - improvise needs one "
                    "(point config.json api_url at an OpenAI-compatible server).")
            return
        if self.insight <= 0:
            ui.warn("No insight charges left (sleep at an inn or use a bedroll to recover).")
            return
        if confirm and not self._confirm(
                f"  Improvise? costs 1 insight ({self.insight} left) [Y/n] "):
            ui.warn("You hold your course - no insight spent.")
            return
        self.insight -= 1
        ui.warn(f"improvise - insight charges left: {self.insight}")
        ctx = self.eng.get_scene_context()
        p = self.eng.player.data
        user = (f"LOCATION: {ctx['room_name']} ({ctx['description'][:250]})\n"
                f"PRESENT: {ctx['npc_presence']}\n"
                f"CONTENTS: {ctx.get('room_contents', 'nothing of note')}\n"
                f"PLAYER: HP {p['hp']}/{p['max_hp']}, AC {self.eng.calculate_ac()}, "
                f"STR {p['stats']['STR']} DEX {p['stats']['DEX']} CON {p['stats']['CON']} "
                f"INT {p['stats']['INT']} WIS {p['stats']['WIS']} CHA {p['stats']['CHA']}\n"
                f"SITUATION: {focus or 'The player wants to try something creative here.'}")
        msgs = [{"role": "system", "content": SYSTEM_IMPROV},
                {"role": "user", "content": user}]
        text, log = self.llm.improvise(msgs, T.SCHEMAS, self.dispatch,
                                       max_steps=self.cfg.get("max_improvise_steps", 5))
        for line in log:
            ui.warn(f"  · {line}")
        if text:
            ui.narration(text)
            self.last_narration = text
        else:
            ui.warn("(the LLM could not resolve the moment - the engine holds.)")

    # ---------- helpers ----------
    def _fmt_move(self, res):
        if "error" in res and "combat_event" not in res:
            return res["error"]
        parts = []
        if res.get("combat_event") == "escaped":
            ma = res.get("monster_attack", {})
            parts.append("You disengage and slip away." if not ma.get("hit")
                         else f"You slip away, taking {ma.get('damage', 0)} damage.")
        elif res.get("combat_event") == "blocked":
            ma = res.get("monster_attack", {})
            parts.append(f"The monster strikes for {ma.get('damage', 0)}!" if ma.get("hit")
                         else "The monster swings wide as it blocks you.")
            parts.append(res["error"])
        else:
            room = res.get("room", {})
            parts.append(f"You move. {room.get('name', 'Somewhere new')}.")
            if res.get("inn_key_unlock"):
                parts.append("You turn the Inn Key (consumed) - the stairway door swings open, and you climb to the guest room.")
            if res.get("control_note"):
                parts.append(res["control_note"])
            if res.get("emerged"):
                parts.append(self.eng.emergence_flavor())
            enc = self.eng._normalize_encounter(room.get("encounter"))
            if enc:
                parts.append(f"A {', '.join(enc)} is here!")
            if res.get("new_floor"):
                parts.append("You set foot on a depth you have never walked before. "
                             "Something, somewhere, notices. (Virtue +1)")
        return " ".join(parts)

    def _obstacle_hint(self):
        room = self._room()
        obs = room.get("obstacles", {})
        if not obs:
            return "The way ahead feels deliberately strange. (type 'improvise' to engage it)"
        lines = []
        for d, ob in obs.items():
            name = ob.get("name", "obstacle")
            desc = E.OBSTACLE_DATA.get(name, "An obstacle blocks the way.")
            verb = "jump" if name in E.GameEngine.GAP_OBSTACLES else (
                "bash" if name in E.GameEngine.BASH_OBSTACLES else "improvise")
            lines.append(f"Way {d.lower()}: {name} - {desc} "
                         f"(try '{verb} {d.lower()}' or 'improvise')")
        return "\n".join(lines)

    def _exits_line(self):
        room = self._room()
        dirs = list(room.get("exits", {}).keys())
        if room.get("stairs_up") and "Up" not in dirs:
            dirs.append("Up")
        if room.get("stairs_down") and "Down" not in dirs:
            dirs.append("Down")
        for d, door in sorted(room.get("doors", {}).items()):
            label = "door (closed)" if door.get("state") != "locked" else "door (locked)"
            dirs.append(f"{d} - {label}")
        for d, ob in sorted(room.get("obstacles", {}).items()):
            dirs.append(f"{d} - blocked ({ob.get('name', 'obstacle')})")
        exits = ", ".join(dirs) or "none"
        extras = []
        if room.get("trap") and room.get("disarmed"):
            extras.append("trap (disarmed)")
        elif room.get("trap") and room.get("trap_spotted"):
            extras.append(f"trap spotted: {room.get('trap_type')}")
        if room.get("obstacles"):
            names = sorted({ob.get("name", "obstacle") for ob in room["obstacles"].values()})
            extras.append("obstacle: " + ", ".join(names))
        if room.get("container") or room.get("chest"):
            extras.append("a container")
        corpse = room.get("corpse")
        if corpse:
            tag = "picked clean" if corpse.get("searched") else "unsearched"
            extras.append(f"the corpse of the {corpse.get('monster', 'creature')} ({tag})")
        return exits + ("  |  " + "; ".join(extras) if extras else "")

    def _flush_food_msgs(self):
        for msg in self.eng.take_food_msgs():
            ui.warn(msg)

    def _move(self, direction):
        """Move, narrate, then render the room panel (narration inside) - HUD is drawn by the tail block."""
        res = self.eng.move_player(direction)
        outcome = self._fmt_move(res)
        text = self.narrate(outcome)
        ui.show_room(self.eng, narration=text, exits_line=self._exits_line())
        ev = res.get("combat_event")
        fought = False
        if ev == "blocked":
            monsters = self.eng._encounter_list()
            if monsters:
                fought = self._combat(monsters[0])
        elif ev is None and "error" not in res:
            monsters = self.eng._encounter_list()
            if monsters:
                fought = self._combat(monsters[0])
        if fought and fought != "dead":
            # repaint the room so the panel reflects the post-fight state
            ui.show_room(self.eng, exits_line=self._exits_line())
        self._flush_food_msgs()
        gone = res.get("drops_gone")
        if gone:
            bits = "; ".join(f"the {n} you dropped {E.DROP_DECAY}" for n, _q in gone)
            ui.warn(bits + ".")
        return text

    def _monster_hp(self, monster):
        mid = self.eng._find_monster(monster)
        pos = self.eng.player.data["position"]
        room_id = f"{pos['x']}_{pos['y']}_{pos['z']}"
        key = f"{room_id}|{monster}"
        if key not in self.eng.combat_state:
            best = self.eng.world.generator.bestiary.get(mid, {})
            self.eng.combat_state[key] = best.get("hp", 10)
        return self.eng.combat_state[key], self.eng.world.generator.bestiary.get(mid, {})

    def _combat(self, monster):
        """Turn-based combat sub-loop. Returns 'won', 'fled' or 'dead'."""
        m_hp, m_data = self._monster_hp(monster)
        m_max = m_data.get("hp", 10)
        all_monsters = self.eng._encounter_list()
        if len(all_monsters) > 1:
            ui.warn("Targets: " + " | ".join(f"{i+1}) {m}" for i, m in enumerate(all_monsters)))
            ui.warn("(type a monster's name or number to switch targets)")
        p = self.eng.player.data
        weapon = p["equipped"].get("weapon")
        w_name = weapon if weapon else "bare hands"
        dark = (not self.eng.room_is_lit()) and not self.eng.player.is_lit()
        ui.warn(f"== COMBAT: {monster} (HP {m_hp}/{m_max})  |  you fight with {w_name}"
                + ("  |  darkness: -4 to hit! ==" if dark else " =="))
        ui.warn("Commands: attack (Enter) / flee / parley <what you try> / "
                "use <item> / cast (mix reagents) / inv / status\n"
                "(any other words are offered as a parley attempt - spend insight "
                "to talk, bribe or hide your way out)")
        while True:
            try:
                raw = ui.prompt("\n(c) > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                raw = "flee"
            parts = raw.split()
            verb = parts[0] if parts else ""
            cast_formula = None
            use_res = None
            if verb in ("", "a", "attack"):
                player_action = "fight"
            elif verb in ("f", "flee", "run"):
                opp = {"North": "South", "South": "North", "East": "West", "West": "East",
                       "Up": "Down", "Down": "Up"}.get(p.get("last_direction"))
                if not opp:
                    exits = list(self._room().get("exits", {}).keys())
                    opp = exits[0] if exits else None
                if not opp:
                    ui.bad("No way out - the fight finds you anyway!")
                    continue
                pos_before = dict(self.eng.player.data["position"])
                res = self.eng.move_player(opp)
                if self.eng.player.data["position"] != pos_before and res.get("combat_event") != "escaped":
                    ui.bad("You have perished - the High Priestess drags your soul back to the Temple of Radiance.")
                    self._respawn()
                    ui.show_room(self.eng, exits_line=self._exits_line())
                    return "dead"
                if res.get("combat_event") == "escaped":
                    ma = res.get("monster_attack", {})
                    ui.warn(ma.get("message", "The monster lashes out as you break away."))
                    self.eng.player.update_virtue(-1)
                    ui.show_room(self.eng, narration="You disengage and put distance between "
                                   "you and the fight. The Protector ran. (Virtue -1)")
                    return "fled"
                ui.bad(res.get("error", "It blocks your path again!"))
                continue
            elif verb == "use":
                target = " ".join(parts[1:])
                if not target:
                    ui.warn("Use what? (e.g. 'use red potion')")
                    continue
                use_res = self.eng.use_item(target)
                ui.say(use_res.get("message", str(use_res)) if isinstance(use_res, dict) else str(use_res))
                if isinstance(use_res, dict) and not use_res.get("success"):
                    continue
                player_action = "none"
            elif verb in ("c", "cast", "mix", "m"):
                cast_formula = self._reagent_menu()
                if cast_formula is None:
                    continue
                player_action = "mix"
            elif verb in ("i", "inv", "inventory"):
                self.cmd_inventory()
                continue
            elif verb == "status":
                ui.show(self.eng, insight=self.insight, insight_max=self.insight_max)
                continue
            elif verb == "parley" or raw:
                outcome = self._parley(monster, m_hp, m_max, raw)
                if outcome == "pacified":
                    return "pacified"
                if outcome == "dead":
                    ui.bad("You have perished - the High Priestess drags your "
                           "soul back to the Temple of Radiance.")
                    self._respawn()
                    ui.show_room(self.eng, exits_line=self._exits_line())
                    return "dead"
                continue
            else:
                ui.warn("In combat: attack, flee, parley <what you try>, "
                        "use <item>, cast, inv, status")
                continue
            # monster AI: mostly fights, sometimes uses a special trait
            specials = m_data.get("special", [])
            monster_action = "special" if specials and random.random() < 0.25 else "fight"
            pos_before = dict(self.eng.player.data["position"])
            res = self.eng.resolve_combat_turn(monster, player_action, weapon, monster_action,
                                      formula=cast_formula)
            pos_after = self.eng.player.data["position"]
            pa = res.get("player_attack", {})
            if player_action == "fight":
                if pa.get("hit"):
                    ui.say(f"You strike the {monster} for {pa['damage']} damage! (roll {pa.get('roll')})")
                else:
                    ui.say(f"You miss the {monster}. (roll {pa.get('roll')})")
            elif player_action == "mix" and pa.get("message"):
                if pa.get("spell") == "Detect Magic":
                    ui.narration(self._detect_prose({"reading": pa["message"].replace("Detect Magic: ", ""),
                                               "auras": pa.get("auras") or []}))
                else:
                    ui.say(pa["message"])
            elif player_action == "none" and not (isinstance(use_res, dict) and use_res.get("success")):
                pass
            ma = res.get("monster_attack", {})
            if ma.get("message") and "perished" not in ma["message"]:
                (ui.bad if ma.get("hit") else ui.say)(ma["message"])
            tick = res.get("poison_tick")
            if tick and tick.get("message"):
                ui.warn(tick["message"])
            for cm in res.get("control_msgs", []):
                ui.say(cm)
            m_hp = res.get("monster_hp", m_hp)
            p = self.eng.player.data
            ui.warn(f"You: {p['hp']}/{p['max_hp']}   {monster}: {max(0, m_hp)}/{m_max}")
            if res.get("player_dead") or pos_after != pos_before:
                ui.bad("You have perished - the High Priestess drags your soul back to the Temple of Radiance.")
                self._respawn()
                ui.show_room(self.eng, exits_line=self._exits_line())
                return "dead"
            if res.get("monster_dead"):
                extra = res.get("virtue_msg") or ""
                ui.say(f"The {monster} falls! Virtue +2." + extra)
                qline = self.eng.notify_kill(monster)
                if qline:
                    ui.warn(qline)
                for line in (res.get("drop_lines") or []):
                    ui.say(line)
                remaining = self.eng._encounter_list()
                if not remaining:
                    return "won"
                # More monsters remain - keep fighting the next one.
                monster = remaining[0]
                m_hp, m_data = self._monster_hp(monster)
                m_max = m_data.get("hp", 10)
                ui.warn(f"\nAnother turns on you: {monster} (HP {m_hp}/{m_max})")
                continue

    def _parley(self, monster, m_hp, m_max, raw):
        """Mid-fight roleplay: spend 1 insight to talk, bargain, bribe or hide
        your way out. The engine rolls the die (resolve_parley); the LLM only
        voices the moment. Returns 'pacified', 'dead', or 'failed'."""
        if self.insight <= 0:
            ui.warn("No insight charges left - fight or flee. "
                    "(insight returns when you sleep)")
            return "failed"
        if not self._confirm(f"  Parley? costs 1 insight ({self.insight} left) [Y/n] "):
            ui.warn("You hold your tongue - the fight waits.")
            return "failed"
        self.insight -= 1
        ui.warn(f"parley - insight charges left: {self.insight}")
        m = re.search(r"(\d+)", raw)
        gold_offered = int(m.group(1)) if m else 0
        p = self.eng.player.data
        ctx = self.eng.get_scene_context()
        user = (f"MONSTER: {monster} (HP {m_hp}/{m_max})\n"
                f"LOCATION: {ctx['room_name']}\n"
                f"CONTENTS: {ctx.get('room_contents', 'nothing of note')}\n"
                f"PLAYER: HP {p['hp']}/{p['max_hp']}, AC {self.eng.calculate_ac()}, "
                f"gold {p['gold']}, CHA {p['stats']['CHA']} DEX {p['stats']['DEX']}\n"
                f"GOLD OFFERED: {gold_offered}\n"
                f"THE PLAYER'S ATTEMPT: {raw}")
        base_dispatch = self.dispatch
        once = {"done": None}

        def disp(name, args):
            args = args or {}
            if name == "resolve_parley":
                if once["done"] is not None:
                    # already resolved this turn - return the cached verdict
                    return json.dumps(once["done"])
                if not args.get("monster"):
                    args = dict(args)
                    args["monster"] = monster
                r = base_dispatch(name, args)
                once["done"] = self.eng.last_parley_verdict or {}
                return r
            return base_dispatch(name, args)

        msgs = [{"role": "system", "content": SYSTEM_COMBAT_IMPROV},
                {"role": "user", "content": user}]
        self.eng.last_parley_verdict = None  # ignore any stale verdict
        text, log = self.llm.improvise(msgs, T.COMBAT_SCHEMAS, disp,
                                       max_steps=self.cfg.get("max_improvise_steps", 5))
        for line in log:
            ui.warn(f"  \u00b7 {line}")
        verdict = self.eng.last_parley_verdict
        if not verdict:
            # The LLM narrated without calling the tool - resolve mechanically.
            verdict = self.eng.resolve_parley(monster, "negotiate", gold_offered)
        if not verdict.get("success"):
            ui.warn(str(verdict.get("error", "the attempt comes to nothing")))
            return "failed"
        if text:
            ui.narration(text)
            self.last_narration = text
        ui.warn(f"[parley {verdict['stat']} check: d20 {verdict['natural']} "
                f"{verdict['modifier']:+d} = {verdict['roll']} vs DC {verdict['dc']}"
                + (f" | {verdict['gold_spent']} gold spent" if verdict.get("gold_spent") else "")
                + (" | stands down!" if verdict["pacified"] else " | it fails!") + "]")
        if verdict["pacified"]:
            return "pacified"
        ma = verdict.get("monster_attack") or {}
        if ma.get("message"):
            (ui.bad if ma.get("hit") else ui.say)(ma["message"])
        tick = verdict.get("poison_tick")
        if tick and tick.get("message"):
            ui.warn(tick["message"])
        if verdict.get("player_dead"):
            return "dead"
        p = self.eng.player.data
        ui.warn(f"You: {p['hp']}/{p['max_hp']}   {monster}: {m_hp}/{m_max}")
        return "failed"


    def cmd_look(self):
        ui.show_room(self.eng, exits_line=self._exits_line())

    def cmd_reset(self):
        """Abandon this run and reset the world to defaults (with confirmation)."""
        ui.warn("Abandon your run? This wipes your character AND the dungeon -")
        ui.warn("all progress on this save is lost. There is no undo.")
        if not self._confirm_destructive("  Are you sure? [y/N] "):
            ui.warn("Run preserved - you continue your adventure.")
            return
        self.eng.reset_game()
        self.insight = self.insight_max
        self.last_narration = None
        ui.say("The world dissolves and reknits itself. You stand once more at the "
               "Temple of Radiance, a Cloth Robe your only possession, the dungeon "
               "yet unexplored.")

    def _freshness(self, name, inv):
        ages = self.eng.player.data.get("item_ages", {})
        limit = E.PERISHABLE_FOODS.get(name)
        if not limit or name not in inv:
            return ""
        left = max(0, limit - ages.get(name, 0))
        frac = left / limit
        word = "fresh" if frac > 2 / 3 else "going" if frac > 1 / 3 else "wilting"
        return f" ({word})"

    def cmd_inventory(self):
        inv = self.eng.player.data["inventory"]
        if not inv:
            ui.warn("You carry nothing.")
            return
        usage = self.eng.player.get_inventory_usage(inv)
        cap = self.eng.player.get_inventory_capacity(inv)
        if ui._is_api():
            ui.say(f"Inventory ({usage}/{cap} slots):")
            for name, qty in sorted(inv.items()):
                ui.say(f"  {name}{self._freshness(name, inv)} x{qty}")
        elif ui.RICH:
            table = ui.Table(box=ui.box.SIMPLE, title=f"Inventory ({usage}/{cap} slots)")
            for name, qty in sorted(inv.items()):
                table.add_row(name + self._freshness(name, inv), str(qty))
            ui.console.print(table)
        else:
            for name, qty in sorted(inv.items()):
                print(f"  {name}{self._freshness(name, inv)} x{qty}")

    def cmd_equip(self, item):
        msg = self.eng.player.equip_item(item)
        ui.say(msg.get("message", str(msg)) if isinstance(msg, dict) else str(msg))

    def cmd_unequip(self, item):
        msg = self.eng.player.unequip_item(item)
        ui.say(msg.get("message", str(msg)) if isinstance(msg, dict) else str(msg))

    def cmd_discard(self, arg):
        """'discard <item> [qty]' - throw it away for good. A bare 'discard'
        shows what you carry so you can name it. When a stack is being discarded
        and no quantity is given, you are asked 'How many?' (default: the lot)."""
        arg = (arg or "").strip()
        inv = self.eng.player.data["inventory"]
        if not arg:
            if not inv:
                ui.warn("You carry nothing to discard.")
                return
            held = ", ".join(f"{k} (x{q})" for k, q in sorted(inv.items()))
            ui.say(f"Discard what? You carry: {held}")
            return
        # Split an optional trailing quantity: 'discard rope 3' -> wanted='rope', q='3'
        tokens = arg.split()
        qty_tok = None
        wanted = arg
        if len(tokens) > 1 and tokens[-1].lstrip("*").isdigit():
            qty_tok = tokens[-1]
            wanted = " ".join(tokens[:-1])
        match, ambiguous = E.resolve_item_name(wanted, [k for k, q in inv.items() if q > 0])
        if ambiguous:
            ui.warn("Which one - " + " or ".join(ambiguous) + "?")
            return
        if not match:
            ui.warn(f"You don't have {wanted} to discard.")
            return
        have = inv.get(match, 0)
        # Decide how many to throw away.
        if qty_tok is not None:
            if qty_tok in ("*", "all"):
                qty = have
            else:
                qty = int(qty_tok)
        else:
            qty = have  # default: the whole stack
        if have > 1 and qty_tok is None:
            # Asked 'How many?' only when it actually matters (a stack, no number given).
            try:
                ans = ui.prompt(f"How many {match}? [1-{have}, default {have}]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                ans = ""
            if ans in ("", "y", "yes", "all", "*"):
                qty = have
            elif ans.isdigit():
                qty = max(1, min(int(ans), have))
            else:
                ui.warn(f"Not a number - discarding {have}x {match}.")
                qty = have
        res = self.eng.player.discard_item(match, qty)
        (ui.say if res.get("success") else ui.warn)(res.get("message", str(res)))


    def cmd_get(self, arg):
        """'get <item>' - pick a dropped item back off the room floor."""
        pos = self.eng.player.data["position"]
        room = self._room()
        drops = room.get("dropped_items") or []
        names = sorted({d["name"] for d in drops})
        if not arg:
            if not drops:
                ui.warn("There is nothing lying on the ground here.")
            else:
                ui.say("Pick up what? Lying here: " + ", ".join(names))
            return
        match, ambiguous = E.resolve_item_name(arg, names)
        if ambiguous:
            ui.warn("Which one - " + " or ".join(ambiguous) + "?")
            return
        if not match:
            lying = ", ".join(names) or "nothing"
            ui.warn(f"There is no {arg} lying here. On the ground: {lying}")
            return
        res = self.eng.player.drop_pickup(pos["x"], pos["y"], pos["z"], match)
        (ui.say if res.get("success") else ui.warn)(res.get("message", str(res)))


    def cmd_talk(self, name=None):
        """Conversational sub-loop. Free - never costs an insight charge.
        Pass a name to pick a specific NPC among several in the room."""
        res = self.eng.talk_to_npc(name)
        if "error" in res:
            ui.warn(res["error"])
            if res.get("choices"):
                ui.warn("  Who do you want to talk to? " + " | ".join(res["choices"]))
            return
        topics = ", ".join(res["knowledge_and_hints"].keys())
        sheet = (f"NPC: {res['npc_name']}. {res['description']} "
                 f"Traits: {', '.join(res['personality_traits'])}. "
                 f"Knowledge: {json.dumps(res['knowledge_and_hints'])[:500]}")
        sys_p = SYSTEM_CONV + "\n" + sheet
        msgs = [{"role": "system", "content": sys_p}]
        ui.warn(f"(conversation with {res['npc_name']} - type your words; 'farewell' to leave)")
        if res.get("shop"):
            ui.warn(f"  {res['npc_name']} has wares for sale - after 'farewell': "
                    "'shop' to browse, 'buy <item>' to purchase.")
        offer = res.get("quest_offer")
        active = res.get("active_quest")
        if offer:
            tn = self.eng._quest_turn_in_name(offer)
            ui.say(f"  {res['npc_name']} leans in with a task:")
            ui.say(f"    \"{offer.get('title', 'A task')}\" - {offer.get('text', '')}")
            ui.warn(f"    Objective: {self.eng._quest_objective_text(offer.get('objective', {}))} - "
                    + (f"return to {tn}" if tn else "completes on the spot")
                    + ". Answer 'yes' to accept or 'no' to decline.")
        elif active:
            ui.warn(f"  (you are on a quest: '{active.get('title')}' - "
                    f"{active.get('progress')}/{active.get('needed')}; "
                    f"{active.get('turn_in')})")
        # In-context greeting: the 'talk' action itself prompts the NPC to open.
        msgs.append({"role": "user", "content":
                     "(The player approaches you. Greet them in character in 1-3 "
                     "sentences. Hint at what you might know about: "
                     + topics + ". Do not quote these keywords stiffly - let them "
                     "color your mood and topic naturally.)"})
        reply = self.llm.chat(msgs, max_tokens=self.cfg.get("conversation_max_tokens", 200))
        if "error" in reply:
            ui.say(f"  {res['npc_name']} regards you, waiting.")
            msgs.pop()
        else:
            text = (reply.get("content") or "").strip()
            if text:
                msgs.append({"role": "assistant", "content": text})
                ui.say(f"  {res['npc_name']}: {text}")
            else:
                ui.say(f"  {res['npc_name']} regards you, waiting.")
                msgs.pop()
        exchanges = 0
        while exchanges < 30:
            try:
                line = ui.prompt("  you > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line.lower() in ("farewell", "bye", "leave", "q", "quit", "exit"):
                ui.say(f"  You part ways with {res['npc_name']}.")
                break
            # A merchant NPC: 'shop'/'buy'/'sell' transact right here instead of
            # going to the LLM as chatter (no insight, no LLM turn).
            if res.get("shop"):
                w0 = line.split(None, 1)
                head = w0[0].lower().rstrip(".!?")
                if head in ("shop", "browse"):
                    self.cmd_shop()
                    continue
                if head in ("buy", "sell"):
                    self._cmd_trade(head, w0[1].strip() if len(w0) > 1 else "")
                    continue
            # A pending quest offer is answered mechanically - the engine
            # holds the offer; the LLM only voices the citizen.
            if self.eng.quest_pending():
                low = line.lower().strip().rstrip(".!?")
                if low in ("yes", "y", "accept", "i will", "deal", "agreed"):
                    ui.say("  " + str(self.eng.accept_quest().get("message", "")))
                    continue
                if low in ("no", "n", "decline", "refuse", "not now"):
                    ui.say("  " + str(self.eng.decline_quest().get("message", "")))
                    continue
            msgs.append({"role": "user", "content": line})
            reply = self.llm.chat(msgs, max_tokens=self.cfg.get("conversation_max_tokens", 200))
            if "error" in reply:
                ui.warn(f"  (they seem not to hear you: {reply['error']})")
                msgs.pop()
                break
            text = (reply.get("content") or "").strip()
            if not text:
                ui.warn("  (a long, drifting silence...)")
                continue
            msgs.append({"role": "assistant", "content": text})
            ui.say(f"  {res['npc_name']}: {text}")
            exchanges += 1
            if "FAREWELL" in text.upper():
                ui.say(f"  You part ways with {res['npc_name']}.")
                break

    def cmd_quest(self, arg=None):
        """The quest desk. Bare 'quest' reports; accept/decline act on the
        pending offer; abandon gives up the active quest (progress lost,
        Virtue -5 but never downward through 0)."""
        sub = (arg or "").strip().lower().rstrip(".!?")
        if sub in ("", "status"):
            st = self.eng.quest_status()
            if st:
                ui.say(f"Quest: {st['title']} - {st['text']}")
                ui.say(f"  Objective: {st['objective']} "
                       f"({st['progress']}/{st['needed']}) - {st['turn_in']}.")
                if st["done"]:
                    ui.warn("  The deed is done - make the hand-in.")
                return
            p = self.eng.quest_pending()
            if p:
                ui.warn(f"An offer awaits from {p.get('giver')}: "
                        f"\"{p.get('title')}\" - {p.get('text')} "
                        f"({p.get('objective')}; {p.get('turn_in')}). "
                        "'quest accept' or 'quest decline'.")
            else:
                ui.warn("No active quest. Talk to a citizen - someone may "
                        "have a task for the Protector.")
            return
        if sub == "accept":
            res = self.eng.accept_quest()
        elif sub in ("decline", "refuse"):
            res = self.eng.decline_quest()
        elif sub == "abandon":
            res = self.eng.abandon_quest()
        else:
            ui.warn("Usage: quest | quest accept | quest decline | quest abandon")
            return
        (ui.say if res.get("success") else ui.warn)(res.get("message", str(res)))

    def cmd_search(self, arg=None):
        room = self._room()
        corpse = room.get("corpse")
        # An explicit 'search corpse' always goes to the corpse (so it can
        # report 'already picked clean'); a bare 'search' loots it only while
        # there is something left inside, otherwise it hunts for traps.
        if corpse and self.eng._name_is_corpse(arg, corpse) and (arg or not corpse.get("searched")):
            ui.say(str(self.eng.search_corpse(arg)))
            return
        if room.get("type") == "Discovery" and room.get("discovery"):
            ui.say(str(self.eng.discovery_search()))
        else:
            ui.say(str(self.eng.search_traps()))
        if self.eng.death_message:
            ui.bad("You have perished - the High Priestess drags your soul back to "
                   "the Temple of Radiance.")
            self._respawn()

    def cmd_disarm(self):
        ui.say(str(self.eng.disarm_trap()))

    def cmd_meditate(self):
        """At a town shrine (the Temple of Radiance), meditate trades 50 Virtue
        for a permanent +1 to a chosen attribute. Dungeon Holy Altars keep their
        own discovery behavior (cure disease + Virtue)."""
        pos = self.eng.player.data["position"]
        room = self.eng.world.get_room(pos["x"], pos["y"], pos["z"])
        # Dungeon altar/discovery rooms keep the old altar behavior.
        if room and room.get("type") == "Discovery":
            self.cmd_discovery("discovery_meditate", "meditate")
            return
        # A shrine room: town Temple has shrine=True; dungeon Shrine rooms have
        # type=="Shrine" (some are negative). Trade virtue for a stat (positive)
        # or offer your soul to the black altar (negative).
        is_shrine_room = room and (room.get("shrine") or room.get("type") == "Shrine")
        if is_shrine_room:
            negative = bool(room.get("shrine_negative"))
            stats = list(self.eng.player.data["stats"].keys())
            if negative:
                ui.warn("The black altar watches you. If your soul is dark enough it may empower you; if it is light, it will curse you. Choose an attribute to offer (or decline).")
            else:
                ui.warn("You may trade 50 Virtue for a permanent +1 to one attribute. Which?")
            for i, s in enumerate(stats, 1):
                ui.say(f"  {i}. {s} ({self.eng.player.data['stats'][s]})")
            try:
                raw = ui.prompt("stat > ").strip().upper()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if raw in ("", "q", "quit", "c", "cancel"):
                ui.warn("You rise from the kneeler, unchanged.")
                return
            if raw.isdigit() and 1 <= int(raw) <= len(stats):
                stat = stats[int(raw) - 1]
            elif raw in stats:
                stat = raw
            else:
                ui.warn("Never mind, then.")
                return
            res = self.eng.meditate("negative_shrine" if negative else "town", stat)
            (ui.say if res.get("success") else ui.warn)(res.get("message", str(res)))
            return
        ui.warn("There is no shrine here to meditate at.")

    def cmd_discovery(self, method_name, label):
        method = getattr(self.eng, method_name, None)
        if method is None:
            ui.warn("You can't do that here.")
            return
        ui.say(str(method()))

    def cmd_open(self, name=None):
        """'open' opens the room's container; 'open <direction>' opens a door."""
        if name and self.eng._resolve_direction(name):
            ui.say(self.narrate(str(self.eng.open_door(name))))
        else:
            ui.say(self.narrate(str(self.eng.open_container(name))))

    def cmd_bash(self, arg=None):
        res = self.eng.bash(arg)
        ui.say(self.narrate(res["text"]))
        if self._room().get("encounter", "None") != "None":
            self._combat(self._room().get("encounter"))
            self.cmd_look()
        else:
            self.cmd_look()

    def cmd_jump(self, arg=None):
        res = self.eng.jump(arg)
        if res.get("moved"):
            self._move(res["direction"])
        else:
            ui.say(self.narrate(res["text"]))
            self.cmd_look()

    def cmd_use(self, item):
        res = self.eng.use_item(item)
        if isinstance(res, dict):
            ui.say(res.get("message", str(res)))
        else:
            ui.say(str(res))

    def cmd_read(self, item):
        res = self.eng.read_item(item or "")
        if isinstance(res, dict):
            ui.say(res.get("message", str(res)))
        else:
            ui.say(str(res))

    def _reagent_menu(self):
        """Ultima-style casting: the player knows the formula out-of-game.
        We list only the reagents actually carried and ask for a quantity of
        each (Enter = 0). Returns a formula dict, or None if cancelled/empty."""
        inv = self.eng.player.data["inventory"]
        carried = {k: v for k, v in inv.items()
                   if k in E.REAGENT_DATA and v > 0}
        if not carried:
            ui.warn("You carry no reagents to mix. (Reagent Pouch keeps up to 20 of each.)")
            return None
        names = sorted(carried)
        ui.warn("Mixing reagents - enter a quantity for each (Enter = 0, 'q' cancels):")
        for i, n in enumerate(names, 1):
            ui.say(f"  {i}. {n:<22} (have {carried[n]})")
        formula = {}
        for n in names:
            try:
                raw = ui.prompt(f"    {n} [0] > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return None
            if raw.lower() in ("q", "quit", "c", "cancel"):
                return None
            if raw == "":
                qty = 0
            else:
                try:
                    qty = int(raw)
                except ValueError:
                    ui.warn(f"    Not a number - using 0 for {n}.")
                    qty = 0
            if qty < 0:
                qty = 0
            if qty > carried[n]:
                ui.warn(f"    You only have {carried[n]} {n} - using {carried[n]}.")
                qty = carried[n]
            if qty > 0:
                formula[n] = qty
        if not formula:
            ui.warn("You mix nothing.")
            return None
        mix = ", ".join(f"{q}x {k}" for k, q in formula.items())
        ui.warn(f"Mixture: {mix}")
        return formula

    def _detect_prose(self, res):
        """Detect Magic: engine decides WHAT registers, the LLM only phrases it."""
        reading = res.get("reading") or "Nothing registers."
        if not self.narrate_on or not self.llm_ready:
            return reading
        ctx = self.eng.get_scene_context()
        auras = res.get("auras") or []
        user = (f"LOCATION: {ctx['room_name']}\n"
                f"SCENE: {ctx['description'][:300]}\n"
                f"PRESENT: {ctx['npc_presence']}\n"
                f"AURAS: {json.dumps(auras)}\n"
                f"ENGINE READING: {reading}")
        text, err = self.llm.narrate(SYSTEM_DETECT, user,
                                    max_tokens=self.cfg.get("detect_max_tokens", 160))
        if err or not text:
            return reading
        self.last_narration = text
        return text

    def _prompt_teleport_coords(self):
        """Prompt for a Teleport destination (x, y, z). Returns (x, y, z) or None."""
        raw = ui.prompt("Where do you know where you're going? (x, y, z) > ").strip()
        if not raw:
            return None
        parts = [p.strip() for p in raw.replace(",", " ").split() if p.strip()]
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            ui.warn("That's not a coordinate - give x, y, z as numbers.")
            return None
        if len(nums) != 3:
            ui.warn("I need exactly three numbers: x, y, z.")
            return None
        return tuple(nums)

    # ---------- batch 4: LLM roleplay sub-loops ----------
    def _roleplay_loop(self, system, sheet, greeting, prompts, max_turns=6,
                       farewell=("farewell", "bye", "leave", "q", "quit", "exit")):
        """Shared roleplay sub-loop (modeled on cmd_talk, stripped of shop/quest).

        The ENGINE has already validated the identity/facts (in `sheet`); the
        LLM only voices the creature/plant. Returns when the player leaves or
        the exchange budget runs out. Falls back to a plain note if no LLM.
        """
        if not self.llm_ready:
            ui.say("  (The magic holds, but there is no voice to answer - "
                   "the LLM is not configured.)")
            return
        msgs = [{"role": "system", "content": system + "\n" + sheet}]
        ui.warn(f"  ({prompts} - 'farewell' to leave)")
        msgs.append({"role": "user", "content": greeting})
        reply = self.llm.chat(msgs, max_tokens=self.cfg.get("conversation_max_tokens", 200))
        if "error" in reply:
            ui.say("  (a silence where a voice should be...)")
            msgs.pop()
        else:
            text = (reply.get("content") or "").strip()
            if text:
                msgs.append({"role": "assistant", "content": text})
                ui.say(f"  {text}")
            else:
                ui.say("  (a silence where a voice should be...)")
                msgs.pop()
        turns = 0
        while turns < max_turns:
            try:
                line = ui.prompt("  you > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line.lower() in farewell:
                ui.say("  The presence settles back into stillness.")
                break
            msgs.append({"role": "user", "content": line})
            reply = self.llm.chat(msgs, max_tokens=self.cfg.get("conversation_max_tokens", 200))
            if "error" in reply:
                ui.warn(f"  (it seems not to hear you: {reply['error']})")
                msgs.pop()
                break
            text = (reply.get("content") or "").strip()
            if not text:
                ui.warn("  (a long, drifting silence...)")
                continue
            msgs.append({"role": "assistant", "content": text})
            ui.say(f"  {text}")
            turns += 1
            if "FAREWELL" in text.upper():
                ui.say("  The presence settles back into stillness.")
                break

    def _commune_loop(self, res):
        """Commune with the Dead: 3 yes/no questions answered AS THE CREATURE
        IT WAS. The corpse's identity + the room facts are the guardrail."""
        name = res.get("corpse", "it")
        kind = res.get("kind", "unknown")
        special = ", ".join(res.get("special", [])) or "none"
        sheet = (f"YOU ARE THE DEAD {name.upper()} (kind: {kind}; traits: {special}). "
                 f"You are a corpse in the room and answer ONLY yes or no. "
                 f"Answer AS THE CREATURE YOU WERE - a beast or a simple creature "
                 f"knows little of the wider dungeon; do not grant it knowledge it "
                 f"could not have had. Be truthful. The room facts below are what "
                 f"is really here - trust them:\n"
                 f"  LOCATION: {res.get('room_name')}\n"
                 f"  CONTENTS: {res.get('room_contents')}")
        greeting = ("(The player kneels by your corpse and asks their first "
                    "question. Answer in ONE short line - yes or no, in "
                    "character. You may add a whisper of context, but the "
                    "answer itself is yes or no.)")
        self._roleplay_loop(SYSTEM_COMMUNE, sheet, greeting,
                            "the dead answers - three yes/no questions")

    def _speak_with_animals_loop(self, res):
        """Speak with Animals: the beast answers questions. Its identity + the
        room facts are the guardrail."""
        animal = res.get("animal", "it")
        kind = res.get("kind", "beast")
        special = ", ".join(res.get("special", [])) or "none"
        sheet = (f"YOU ARE THE {animal.upper()} (kind: {kind}; traits: {special}). "
                 f"You are a living beast and answer the player's questions. "
                 f"Answer AS THE ANIMAL YOU ARE - you know the immediate area "
                 f"(what you have seen, smelled, heard) but little of the wider "
                 f"dungeon; do not grant yourself knowledge you could not have "
                 f"had. Be truthful. The room facts below are what is really "
                 f"here - trust them:\n"
                 f"  LOCATION: {res.get('room_name')}\n"
                 f"  CONTENTS: {res.get('room_contents')}")
        greeting = ("(The player speaks to you. Greet them in 1-3 short lines "
                    "in character, then answer whatever they ask. You are a "
                    "beast - keep it simple and true.)")
        self._roleplay_loop(SYSTEM_SPEAK, sheet, greeting,
                            f"the {animal} answers")

    def _speak_with_plants_loop(self, res):
        """Speak with Plants: the plant reveals a page, a lore line, or a
        secret. The ENGINE picks the validated fact (a readable from the
        ledger); the LLM only roleplays the delivery."""
        if not self.llm_ready:
            ui.say("  (The thorns whisper, but no words reach you - the LLM "
                   "is not configured.)")
            return
        key = self.eng._speak_with_plants_reveal()
        if not key:
            ui.say("  The thorns curl and say nothing more. (No secret to give here.)")
            return
        text = (key.get("text") or "").strip()
        kind = key.get("kind", "page")
        label = key.get("name", "a page")
        sheet = (f"YOU ARE THE PLANTS IN THIS ROOM (a wall of thorns). The "
                 f"ENGINE has decided the secret you will reveal - a "
                 f"{kind} the player will learn of. Reveal it in character, in "
                 f"1-3 lines of thorn-whisper, as if the plants are telling "
                 f"the player what it is or where to find it. Do NOT invent "
                 f"new secrets or change the content. The secret:\n"
                 f"  {label}\n  \"{text}\"")
        greeting = ("(The player asks the plants what they know. Reveal the "
                    "secret the engine gave you, in 1-3 lines of "
                    "character - thorn-whisper. Then the player may follow up "
                    "briefly.)")
        self._roleplay_loop(SYSTEM_SPEAK, sheet, greeting,
                            "the plants whisper a secret")

    def _wish_loop(self, res):
        """Wish: the player describes the effect; the LLM gets ALL tools and
        executes the plan as closely as possible. The engine stays authoritative."""
        if not self.llm_ready:
            ui.say("  (The potential blazes and fades - the LLM is not "
                   "configured to grant a wish.)")
            return
        ui.warn("  (Describe what you wish for - the LLM will try to make it "
                "happen with the game's own tools. The engine stays in charge.)")
        try:
            wish = ui.prompt("  your wish > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not wish:
            ui.say("  The potential fades unspent. (The reagents are spent.)")
            return
        ctx = self.eng.get_scene_context()
        p = self.eng.player.data
        user = (f"LOCATION: {ctx['room_name']} ({ctx['description'][:250]})\n"
                f"PRESENT: {ctx['npc_presence']}\n"
                f"CONTENTS: {ctx.get('room_contents', 'nothing of note')}\n"
                f"PLAYER: HP {p['hp']}/{p['max_hp']}, AC {self.eng.calculate_ac()}, "
                f"gold {p['gold']}\n"
                f"THE WISH: {wish}")
        msgs = [{"role": "system", "content": SYSTEM_WISH},
                {"role": "user", "content": user}]
        text, log = self.llm.improvise(msgs, T.SCHEMAS, self.dispatch,
                                       max_steps=self.cfg.get("max_improvise_steps", 5))
        for line in log:
            ui.warn(f"  \u00b7 {line}")
        if text:
            ui.narration(text)
            self.last_narration = text
        else:
            ui.warn("  (The wish strains against the game's rules and comes "
                    "to nothing - the engine holds.)")

    def cmd_cast(self, in_combat=False):
        """Combine reagents into a spell. Returns the engine result dict (or None)."""
        formula = self._reagent_menu()
        if formula is None:
            return None
        res = self.eng.mix_reagents(formula)
        if isinstance(res, dict):
            # Teleport / Locate: the reagents are mixed; now prompt for the
            # coordinate / target, then apply. (Out-of-combat only - mixing
            # these mid-fight just shows the ready state and spends the turn.)
            if not in_combat and res.get("spell") == "Teleport" and res.get("needs_coords"):
                ui.say(res.get("message", ""))
                res = self.eng._teleport_apply(self._prompt_teleport_coords())
            elif not in_combat and res.get("spell") == "Locate Object or Person" and res.get("needs_target"):
                ui.say(res.get("message", ""))
                target = ui.prompt("What do you want to find? > ").strip()
                res = self.eng._locate_apply(target)
            elif not in_combat and res.get("spell") == "Polymorph" and res.get("needs_target"):
                ui.say(res.get("message", ""))
                creature = ui.prompt("What do you wish to become? > ").strip()
                res = self.eng._polymorph_apply(creature)
            elif not in_combat and res.get("spell") == "Telekinesis" and res.get("needs_target"):
                ui.say(res.get("message", ""))
                action = ui.prompt("What do you do? (throw <object> / pull <object>) > ").strip()
                res = self.eng._telekinesis_apply(action)
            elif not in_combat and res.get("spell") == "Commune with the Dead" and res.get("needs_target"):
                ui.say(res.get("message", ""))
                self._commune_loop(res)
                return res
            elif not in_combat and res.get("spell") == "Speak with Animals" and res.get("needs_target"):
                ui.say(res.get("message", ""))
                self._speak_with_animals_loop(res)
                return res
            elif not in_combat and res.get("spell") == "Speak with Plants":
                ui.say(res.get("message", ""))
                self._speak_with_plants_loop(res)
                return res
            elif not in_combat and res.get("spell") == "Wish" and res.get("needs_wish"):
                ui.say(res.get("message", ""))
                self._wish_loop(res)
                return res
            if res.get("spell") == "Detect Magic":
                ui.narration(self._detect_prose(res))
            elif res.get("void_death"):
                # Out-of-bounds Teleport materialization: custom death, respawn,
                # and clear death_message so the redraw tail doesn't double-handle.
                ui.bad(res.get("message", ""))
                self._respawn()
                self.eng.death_message = None
            else:
                ui.say(res.get("message", str(res)))
            return res
        ui.say(str(res))
        return {"success": True, "message": str(res)}

    def cmd_sleep(self):
        res = self.eng.sleep()
        if isinstance(res, dict):
            ui.say(res.get("message", str(res)))
            ok = res.get("success", False)
        else:
            ui.say(str(res))
            ok = True
        if ok:
            self.insight = self.insight_max
            ui.warn(f"You feel renewed. Insight restored ({self.insight}/{self.insight_max}).")
        else:
            ui.warn("You did not truly rest - insight unchanged.")

    def cmd_time(self):
        label = self.eng.time_label()
        if label:
            ui.say(f"It is {label}.")
        else:
            ui.warn("Underground, you have no notion of the hour. Time is "
                    "a rumour the surface tells you.")

    def cmd_roll(self, arg):
        try:
            if arg.startswith("d"):
                parts = arg[1:].split("+")
                sides = int(parts[0])
                mod = int(parts[1]) if len(parts) > 1 else 0
            else:
                sides, mod = 20, int(arg) if arg else 0
        except ValueError:
            sides, mod = 20, 0
        ui.say(f"You roll 1d{sides}{mod:+d} -> {self.eng.roll_dice(sides, mod)}")

    # ---------- trade ----------
    def _resolve_item(self, wanted, candidates):
        """Fuzzy-match a typed item name against a list of exact names.

        Delegates to the engine's resolver so buying, selling, equipping and
        using all forgive case, plurals, quantities and word order.
        """
        match, ambiguous = E.resolve_item_name(wanted, candidates)
        if ambiguous:
            ui.warn("Did you mean: " + ", ".join(ambiguous) + "?")
        return match

    def cmd_shop(self):
        shop_name, shop_data = self.eng._get_current_shop()
        if not shop_data:
            ui.warn("There is no merchant here to trade with.")
            return
        inv = self.eng.get_shop_inventory(shop_name)
        api = ui._is_api()
        if api:
            ui.say(f"{shop_name} deals in:  (buy <item> [qty] / sell <item> [qty])")
        elif ui.RICH:
            from rich.markup import escape
            ui.console.print(f"[bold]{escape(shop_name)}[/bold] deals in:  "
                             "(buy <item> \\[qty] / sell <item> \\[qty])")
        else:
            print(f"{shop_name} deals in:  (buy <item> [qty] / sell <item> [qty])")
        for label, items in inv.items():
            if not items:
                continue
            listing = ", ".join(f"{it['item']} ({it['price']}g)" for it in items)
            if api:
                ui.say(f"  {label}: {listing}")
            elif ui.RICH:
                from rich.markup import escape
                ui.console.print(f"  [cyan]{label}:[/cyan] {escape(listing)}")
            else:
                print(f"  {label}: {listing}")

    def _cmd_trade(self, action, arg):
        shop_name, shop_data = self.eng._get_current_shop()
        if not shop_data:
            ui.warn("There is no merchant here to trade with.")
            return
        if not arg:
            if action == "buy":
                self.cmd_shop()
            else:
                ui.warn("Sell what? Usage: sell <item> [qty]")
            return
        if action == "buy":
            candidates = []
            inv = self.eng.get_shop_inventory(shop_name)
            for items in inv.values():
                candidates.extend(it["item"] for it in items)
        else:
            candidates = list(self.eng.player.data["inventory"].keys())
        item_arg, qty = arg, 1
        first, _, rest = arg.partition(" ")
        if first.isdigit() and rest:            # "buy 2 sling"
            item_arg, qty = rest, int(first)
        else:
            parts = arg.rsplit(None, 1)         # "buy sling 2"
            if len(parts) == 2 and parts[1].isdigit():
                item_arg, qty = parts[0], int(parts[1])
        item = self._resolve_item(item_arg, candidates)
        if not item:
            ui.bad(f"No match for '{item_arg}' here. Type 'shop' to see wares.")
            return
        res = self.eng.trade_item(item, action, qty)
        msg = res.get("message") or str(res)
        (ui.say if res.get("success") else ui.bad)(msg)
        if action == "buy" and res.get("success") and shop_name == "The Gilded Flagon Inn":
            self._offer_tip()

    def _offer_tip(self):
        """The Flagon always asks: how much do you really pay?"""
        gold = self.eng.player.data["gold"]
        try:
            raw = ui.prompt(f"  How much do you pay? (gold: {gold}, Enter = as-is) > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not raw:
            return
        if not raw.isdigit():
            ui.warn("The barmaid counts imaginary coins and shrugs - no tip taken.")
            return
        tip = int(raw)
        if tip <= 0:
            return
        if tip > gold:
            ui.bad("You reach for coin you do not have. The barmaid's eye narrows - "
                   "no tip thrown in.")
            return
        self.eng.player.update_gold(-tip)
        if self.eng.tip_virtue():
            ui.say(f"You slide {tip} gold across the bar. The barmaid smiles at "
                   f"{tip - 1} of it. (Virtue +1)")
        else:
            ui.say(f"You slide {tip} gold across the bar. Heaven is generous, "
                   "but not twice in one day.")

    # ---------- main loop ----------
    MOVE_VERBS = ("n", "s", "e", "w", "u", "d",
                  "north", "south", "east", "west", "up", "down")
    # Commands that leave the screen alone: conversation and merchant
    # browsing keep their transcripts visible instead of being pushed away
    # by a room/HUD repaint.
    QUIET_CMDS = ("save", "help", "?", "roll", "narrate",
                  "talk", "shop", "browse", "bash", "jump")
    HELP = (
        "FREE COMMANDS (no insight cost):\n"
        "  move      n / s / e / w / u / d   (north, south, east, west, up, down)\n"
        "  look      describe the room again\n"
        "  talk      free-flowing conversation with an NPC here (talk <name> to\n"
        "            pick one of several; exit: q)\n"
        "  confer    report your deeds to Lord Raika at the Castle (capped +3, once per sleep)\n"
        "  quest     your current task: quest | quest accept | quest decline |\n"
        "            quest abandon (citizens offer quests to you in 'talk';\n"
        "             completing one is +5 Virtue - abandoning costs -5,\n"
        "             never below 0; one quest at a time)\n"
        "  inv       inventory | equip <item> | unequip <item>\n"
        "            (names are forgiving: case, plurals and half a name are\n"
        "             enough - 'equip torch' finds 'Torches (6)')\n"
        "  discard   discard <item> [qty] - throw it away for good\n"
        "            (drop / throw work too; a stack asks 'How many?',\n"
        "             default the lot - and it empties any worn slot;\n"
        "             what you drop lies here a few moves before it fades)\n"
        "  get       get <item> - pick a dropped item back up (take / grab)\n"
        "  search    hunt for traps/hidden things - costs an hour, and a\n"
        "              fumble can set the trap off under your own fingers\n"
        "            (search [corpse] - rifle the corpse a slain monster left;\n"
        "             its spoils wait inside until you take them)\n"
        "  disarm    disarm a trap you found\n"
        "  open      open [name] - door, gate, container, corpse\n"
        "  bash      bash [way] - smash a locked chest, a locked door, a\n"
        "            cave-in or a portcullis open (STR check). It is NOISY:\n"
        "            a 1-in-6 chance of drawing an ambush\n"
        "  jump      jump <way> - leap a broken floor (DEX check)\n"
        "  attack    attack / fight - engage the monster in this room\n"
        "            (combat auto-starts when you walk into one anyway)\n"
        "  pray      pray / meditate at a shrine\n"
        "  shop      browse a merchant's wares (shop rooms, or a vendor you\n"
        "            'talk' to - e.g. Slippery Pete in the Market District)\n"
        "  buy       buy <item> [qty] | sell <item> [qty] - CHA helps selling\n"
        "            (you can shop/buy/sell right inside a 'talk' too)\n"
        "  use       use <item>  ('eat' and 'consume' work for food)\n"
        "  read      read <item> - look a page/scrap/book over ('study'/'peruse'\n"
        "            work too). Not consumed - the formula is yours to keep in your\n"
        "            own head. Bare 'read' lists what you carry.\n"
        "  cast      mix reagents into a spell (menu of what you carry; cast in combat too)\n"
        "  sleep     bedroll (dungeon) or the inn - restores insight & HP, costs Satiety\n"
        "            (inn: wakes you the next Morning; bedroll: two phases pass)\n"
        "  time      what day is it, and what hour? (no notion of time below)\n"
        "  roll      roll [dN+M] - ad-hoc die roll\n"
        "  status    full character panel\n"
        "  narrate   toggle LLM prose | save | help | quit\n"
        "  reset     abandon this run & reset the world to defaults (asks first)\n"
        "\n"
        "INSIGHT (WIS-based, spent on unorthodox problem-solving):\n"
        "  improvise / i [what you try]   - asks the GM to resolve anything else\n"
        "  Anything the parser doesn't recognise is offered as an improvise,\n"
        "  and you are asked [Y/n] before a charge is spent.\n"
        "\n"
        "COMBAT (auto-starts when a monster blocks your way):\n"
        "  attack (or Enter)  swing your equipped weapon\n"
        "  flee               disengage back the way you came\n"
        "  parley <attempt>   roleplay your way out - talk / bargain / bribe\n"
        "                     <N> / intimidate / charm / hide (costs 1 insight;\n"
        "                     the engine rolls CHA - or DEX to hide - vs the\n"
        "                     monster; a stand-down leaves no corpse or loot).\n"
        "                     Any unrecognised words in combat are offered as a\n"
        "                     parley. A failed parley gives the monster a free\n"
        "                     blow. Intelligent monsters are far easier to talk\n"
        "                     to than mindless beasts or the undead.\n"
        "  use <item>         drink/eat one, costs your turn\n"
        "  cast (or mix)      combine reagents via the quantity menu - costs your turn\n"
        "  inv / status       free look at your kit mid-fight"
    )

    def process_command(self, raw):
        """Dispatch one command line, then redraw the room + HUD.

        Returns {'quit': bool}. This is the Phase 1 seam: the REPL run() loop
        and the web API (api.py) both drive the game through this single method,
        so behaviour is identical and the save format is untouched.
        """
        self.eng.death_message = None
        parts = raw.split(None, 1)
        verb = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else None

        if verb in ("quit", "q", "exit"):
            self.eng.player.save()
            self.eng.world.save()
            ui.say("Farewell.")
            return {"quit": True}
        elif verb in ("save",):
            self.eng.player.save()
            self.eng.world.save()
            ui.say("Saved.")
        elif verb in ("reset", "abandon"):
            self.cmd_reset()
        elif verb in ("help", "?"):
            ui.warn(self.HELP)
        elif verb in ("n", "north"):
            self._move("North")
        elif verb in ("s", "south"):
            self._move("South")
        elif verb in ("e", "east"):
            self._move("East")
        elif verb in ("w", "west"):
            self._move("West")
        elif verb in ("u", "up"):
            self._move("Up")
        elif verb in ("d", "down"):
            self._move("Down")
        elif verb == "look":
            pass  # the redraw tail below paints the room + HUD
        elif verb in ("inv", "inventory"):
            self.cmd_inventory()
        elif verb == "equip":
            self.cmd_equip(arg or "")
        elif verb == "unequip":
            self.cmd_unequip(arg or "")
        elif verb in ("discard", "drop", "throw"):
            self.cmd_discard(arg or "")
        elif verb in ("get", "take", "grab"):
            self.cmd_get(arg or "")
        elif verb == "talk":
            self.cmd_talk(arg or "")
        elif verb == "alms":
            res = self.eng.alms(arg)
            (ui.say if res.get("success") else ui.warn)(res.get("message", str(res)))
        elif verb == "confess":
            res = self.eng.confess()
            (ui.say if res.get("success") else ui.warn)(res.get("message", str(res)))
        elif verb == "confer":
            res = self.eng.confer()
            (ui.say if res.get("success") else ui.warn)(res.get("message", str(res)))
        elif verb == "quest":
            self.cmd_quest(arg)
        elif verb == "search":
            self.cmd_search(arg)
        elif verb == "disarm":
            self.cmd_disarm()
        elif verb in ("attack", "fight"):
            # Web hotspot chip: engage the monster occupying this room
            # (combat otherwise auto-starts on entry; this covers reloads
            # or returns where the beast is still standing its ground).
            monsters = self.eng._encounter_list()
            if not monsters:
                ui.warn("There is nothing here to fight.")
            else:
                self._combat(monsters[0])
        elif verb in ("drink", "sip"):
            self.cmd_discovery("discovery_drink", "drink")
        elif verb in ("meditate", "pray"):
            self.cmd_meditate()
        elif verb in ("wade", "swim", "bathe"):
            self.cmd_discovery("discovery_wade", "wade")
        elif verb == "enter":
            self.cmd_discovery("discovery_enter", "enter")
        elif verb == "open":
            self.cmd_open(arg)
        elif verb in ("use", "eat", "consume"):
            self.cmd_use(arg or "")
        elif verb in ("read", "study", "peruse"):
            self.cmd_read(arg or "")
        elif verb in ("cast", "mix", "m"):
            self.cmd_cast()
        elif verb == "sleep":
            self.cmd_sleep()
        elif verb == "time":
            self.cmd_time()
        elif verb in ("shop", "browse"):
            self.cmd_shop()
        elif verb == "buy":
            self._cmd_trade("buy", arg)
        elif verb == "sell":
            self._cmd_trade("sell", arg)
        elif verb == "roll":
            self.cmd_roll(arg or "")
        elif verb == "improvise" or verb == "i":
            self.improvise(arg)
        elif verb == "narrate":
            self.narrate_on = not self.narrate_on
            ui.warn(f"LLM narration {'ON' if self.narrate_on else 'OFF'}.")
        elif verb == "status":
            pass  # the redraw tail below paints the room + HUD
        elif verb in ("bash",):
            self.cmd_bash(arg)
        elif verb in ("jump", "leap", "hop"):
            self.cmd_jump(arg)
        else:
            # parser miss -> offer improvise (confirmed, costs insight)
            if self.insight > 0:
                self.improvise(raw, confirm=True)
            else:
                ui.warn("Unknown command. Type 'help'.")

        # --- redraw tail: room + HUD after every action, so state-changing
        # commands (equip, use, buy, sleep...) never leave a stale screen.
        for msg in self.eng.take_food_msgs():
            ui.warn(msg)
        done = self.eng.check_quest_completion()
        if done:
            ui.say(done["message"])
        if verb in self.MOVE_VERBS:
            # _move already painted the room panel (with narration)
            if self.should_improvise() and self.insight > 0 and self.llm_ready:
                ui.warn(self._obstacle_hint())
            ui.show(self.eng, insight=self.insight, insight_max=self.insight_max)
        elif verb not in self.QUIET_CMDS:
            self.cmd_look()
            ui.show(self.eng, insight=self.insight, insight_max=self.insight_max)
        return {"quit": False}

    def snapshot(self):
        """Structured, JSON-serialisable view of the current state.

        The web frontend reads this instead of parsing prose: room + exits +
        interactive hotspots (Phase 3 will make these clickable) + a full
        player/HUD block. The terminal path never calls it.
        """
        ctx = self.eng.get_scene_context()
        p = self.eng.player.data
        pos = p["position"]
        room = self.eng.world.get_room(pos["x"], pos["y"], pos["z"])
        room_id = f"{pos['x']}_{pos['y']}_{pos['z']}"

        exits = []
        for d in room.get("exits", {}):
            exits.append({"dir": d, "kind": "exit"})
        if room.get("stairs_up") and "Up" not in room.get("exits", {}):
            exits.append({"dir": "Up", "kind": "stairs"})
        if room.get("stairs_down") and "Down" not in room.get("exits", {}):
            exits.append({"dir": "Down", "kind": "stairs"})
        for d, door in sorted(room.get("doors", {}).items()):
            exits.append({"dir": d, "kind": "door",
                          "state": door.get("state", "closed")})
        for d, ob in sorted(room.get("obstacles", {}).items()):
            exits.append({"dir": d, "kind": "obstacle",
                          "name": ob.get("name", "obstacle")})

        hotspots = []
        npc = E.NPC_REGISTRY.get(room_id)
        if npc is not None:
            # Every hotspot carries a label + (optional) cmd: the web shell
            # renders cmd-carrying chips as buttons whose click fires that
            # exact engine command. The terminal path never sees this.
            hotspots.append({"type": "npc", "name": npc.name,
                             "label": npc.name, "cmd": "talk"})
        enc = room.get("encounter", "None")
        if enc and enc != "None":
            hotspots.append({"type": "monster", "name": enc,
                             "label": enc, "cmd": "attack"})
        cont = room.get("container") or (room.get("chest") or {}).get("item")
        if cont or room.get("chest"):
            cname = cont or "chest"
            hotspots.append({"type": "container", "name": cname,
                             "label": cname, "cmd": "open"})
        if room.get("trap"):
            spotted = bool(room.get("trap_spotted"))
            disarmed = bool(room.get("disarmed"))
            kind = room.get("trap_type")
            h = {"type": "trap", "spotted": spotted, "disarmed": disarmed,
                 "kind": kind}
            if disarmed:
                h["label"] = "trap (disarmed)"
            elif spotted:
                h["label"] = f"trap: {kind}"
                h["cmd"] = "disarm"
            else:
                h["label"] = "something in the floor"
            hotspots.append(h)
        corpse = room.get("corpse")
        if corpse:
            if corpse.get("searched"):
                clabel = f"corpse of the {corpse.get('monster', 'creature')} (picked clean)"
            else:
                clabel = f"corpse of the {corpse.get('monster', 'creature')}"
            hotspots.append({"type": "corpse", "monster": corpse.get("monster"),
                             "searched": bool(corpse.get("searched")),
                             "label": clabel,
                             "cmd": None if corpse.get("searched") else "search corpse"})
        if room.get("shrine") or room.get("type") == "Shrine":
            hotspots.append({"type": "shrine", "label": "shrine", "cmd": "pray"})
        drop_counts = {}
        for d in (room.get("dropped_items") or []):
            drop_counts[d["name"]] = drop_counts.get(d["name"], 0) + 1
        drop_list = [{"name": n, "qty": q} for n, q in sorted(drop_counts.items())]
        for d in drop_list:
            hotspots.append({"type": "drop", "name": d["name"], "qty": d["qty"],
                             "label": f"pick up {d['name']}" + (f" \u00d7{d['qty']}" if d["qty"] > 1 else ""),
                             "cmd": f"get {d['name']}"})

        inv = p["inventory"]
        usage = self.eng.player.get_inventory_usage(inv)
        cap = self.eng.player.get_inventory_capacity(inv)
        return {
            "room": {
                "id": room_id,
                "name": ctx["room_name"],
                "description": ctx["description"],
                "visual_elements": ctx["visual_elements"],
                "npc_presence": ctx["npc_presence"],
                "contents": ctx.get("room_contents", ""),
                "discovery_hint": ctx.get("discovery_hint"),
                "theme": room.get("theme"),
                "type": room.get("type"),
                "time_of_day": ctx.get("time_of_day"),
                "exits": exits,
                "hotspots": hotspots,
                "dropped_items": drop_list,
            },
            "player": {
                "hp": p["hp"], "max_hp": p["max_hp"],
                "satiety": p["satiety"], "max_satiety": p["max_satiety"],
                "gold": p["gold"], "virtue": p["virtue"],
                "ac": self.eng.calculate_ac(),
                "stats": dict(p["stats"]),
                "status_effects": list(p["status_effects"]),
                "equipped": dict(p["equipped"]),
                "position": dict(pos),
                "floor": "Town" if pos["z"] == 0 else f"Depth {abs(pos['z'])}",
                "inventory": [{"name": n, "qty": q} for n, q in sorted(inv.items())],
                "inv_usage": usage, "inv_cap": cap,
            },
            "insight": self.insight,
            "insight_max": self.insight_max,
            "quest": self.eng.quest_status(),
            "quest_pending": self.eng.quest_pending(),
            "narrate": self.narrate_on,
            "llm_ready": self.llm_ready,
            "hud_text": ui.render_hud(self.eng, insight=self.insight,
                                      insight_max=self.insight_max),
        }

    def run(self):
        """Terminal REPL - now a thin adapter over process_command()."""
        ui.console.clear() if ui.RICH else None
        ui.say("LUMINARIA - the city that forgets itself.")
        ui.warn(self.narration_notice())
        ui.warn(self.HELP)
        ui.show(self.eng, insight=self.insight, insight_max=self.insight_max)
        self.cmd_look()
        while True:
            try:
                raw = ui.prompt("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not raw:
                continue
            if self.process_command(raw).get("quit"):
                break

def main():
    cfg = load_cfg()
    Game(cfg).run()


if __name__ == "__main__":
    main()
