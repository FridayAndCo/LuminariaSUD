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

"""ui.py - HUD/room rendering for LuminariaSUD.

All output goes through io_layer.SINK so the same code drives the terminal
(print/rich) and the web API (buffered JSON messages). Rich is optional: the
terminal gets colour panels, the API always receives clean, tag-free text.
"""

import re

from io_layer import TerminalIO

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    from rich import box
    RICH = True
except ImportError:  # graceful plain-text fallback
    RICH = False

console = Console() if RICH else None

# The active sink. luminaria.py (REPL) leaves this as TerminalIO; the API
# server swaps in a QueueIO for its worker thread.
SINK = TerminalIO()

_MARKUP = re.compile(r"\[/?[a-z0-9 #_\.]+\]")


def strip_markup(text):
    return _MARKUP.sub("", text)


def set_sink(sink):
    global SINK
    SINK = sink


_COLOR = {"say": "green", "warn": "yellow", "bad": "red"}


def _out(level, text):
    """Route a message to the sink. Terminal renders rich colour; API buffers text."""
    if _is_api():
        SINK.emit(level, text)
    elif RICH:
        console.print(f"[{_COLOR.get(level, 'white')}]{text}[/]")
    else:
        print(text)


def _bar(value, vmax, width=10):
    if vmax <= 0:
        vmax = 1
    filled = max(0, min(width, int(round(width * value / vmax))))
    return "\u2588" * filled + "\u2591" * (width - filled)


def _stat_line(stats):
    parts = []
    for k, v in stats.items():
        bonus = v - 10
        parts.append(f"{k} {v} ({bonus:+d})")
    return "  ".join(parts)


def render_hud(engine, last_narration=None, insight=None, insight_max=None):
    p = engine.player.data
    ac = engine.calculate_ac()
    light = []
    eq_h = p["equipped"]
    fuel_h = p["light_fuel"]
    # A carried light only burns if it is EQUIPPED (matches engine is_lit()).
    if "Torch" in eq_h.values() and fuel_h.get("torch"):
        light.append(f"Torch x{fuel_h['torch']}")
    if "Lantern" in eq_h.values() and fuel_h.get("lantern"):
        light.append(f"Lantern oil x{fuel_h['lantern']}")
    if p.get("light_duration"):
        light.append(f"Spell light {p['light_duration']}t")
    if light:
        light_str = ", ".join(light)
    elif p["position"]["z"] < 0:
        light_str = "Roomlight" if engine.room_is_lit() else "DARK"
    else:
        light_str = {"Evening": "Dusk", "Night": "Lamplight"}.get(
            engine._phase_name(), "Daylight")
    eq = p["equipped"]
    equip_str = " / ".join(str(v) for v in eq.values() if v) or "nothing"
    status = ", ".join(p["status_effects"]) if p["status_effects"] else "\u2014"
    pos = p["position"]
    floor = "Town" if pos["z"] == 0 else f"Depth {abs(pos['z'])}"

    inv = p["inventory"]
    usage = engine.player.get_inventory_usage(inv)
    cap = engine.player.get_inventory_capacity(inv)
    if inv:
        inv_str = ", ".join(f"{n} x{q}" if q > 1 else n for n, q in sorted(inv.items()))
    else:
        inv_str = "empty"

    time_str = engine.time_label()
    if time_str is None:
        time_str = "??? (no daylight below)"
    lines = [
        f"HP  {_bar(p['hp'], p['max_hp'])} {p['hp']}/{p['max_hp']}   AC {ac}   Gold {p['gold']}",
        f"SAT {_bar(p['satiety'], p['max_satiety'])} {p['satiety']}/{p['max_satiety']}   Virtue {p['virtue']:+d}",
        f"Stats: {_stat_line(p['stats'])}",
        f"Equip: {equip_str}   Light: {light_str}",
        f"Inv [{usage}/{cap}]: {inv_str}",
        f"Pos: ({pos['x']},{pos['y']},{pos['z']}) [{floor}]   Status: {status}",
        f"Time: {time_str}",
    ]
    if insight is not None and insight_max is not None:
        lines.append(f"Insight {_bar(insight, insight_max)} {insight}/{insight_max} (WIS)")
    return "\n".join(lines)


def show(engine, narration=None, plain=False, insight=None, insight_max=None):
    """Render the HUD. Terminal: rich panel. API: clean text via sink."""
    text = render_hud(engine, narration, insight, insight_max)
    if RICH and not plain and not _is_api():
        console.print(Panel(text, title="LUMINARIA", border_style="cyan",
                            box=box.ROUNDED))
    else:
        SINK.panel("hud", text, title="LUMINARIA")


def show_room(engine, narration=None, exits_line=None):
    ctx = engine.get_scene_context()
    room = ctx["room_name"]
    if ctx.get("time_of_day"):
        room = f"{room} - {ctx['time_of_day']}"
    body = ctx["description"]
    if ctx["visual_elements"] and ctx["visual_elements"] != "none":
        body += f"\nDetails: {ctx['visual_elements']}"
    if ctx["npc_presence"]:
        body += f"\n{ctx['npc_presence']}"
    if ctx.get("discovery_hint"):
        body += f"\nDiscovery: {ctx['discovery_hint']}"
    if ctx.get("shrine_slot"):
        body += "\nA shrine stands here - try 'meditate', or feed the coin slot ('confess')."
    if exits_line:
        body += f"\nExits: {exits_line}"
    if narration:
        body += f"\n{narration}"

    if _is_api():
        SINK.panel("room", body, title=room)
        return
    if not RICH:
        print(f"== {room} ==\n{body}")
        return
    console.print(Panel(body, title=room, border_style="blue", box=box.ROUNDED,
                        title_align="center"))


def _is_api():
    """True when a QueueIO sink is active (web mode)."""
    return type(SINK).__name__ == "QueueIO"


def say(text):
    _out("say", text)


def warn(text):
    _out("warn", text)


def bad(text):
    _out("bad", text)


def prompt(text):
    """Blocking read routed through the sink (terminal input / web answer)."""
    return SINK.prompt(text)


# Backwards-compatible aliases used by luminaria.py's inventory/shop blocks.
if RICH:
    from rich.table import Table  # noqa: E402,F401
    from rich import box  # noqa: E402,F401
else:
    Table = None
    box = None
