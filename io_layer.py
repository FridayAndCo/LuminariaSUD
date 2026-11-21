"""io_layer.py - swappable output/input sink for LuminariaSUD.

The game code (luminaria.py) never calls print()/input() directly; it goes
through ui.py, which routes here. That single seam is what lets the exact same
engine + game logic run under:

  * TerminalIO - the classic REPL (print / input), and
  * QueueIO    - the API server: outputs are buffered into a per-command
                 message list; prompts block on a queue until the web client
                 answers (used by the combat sub-loop, conversations, menus).

This is Phase 1 of the graphical refactor: no behaviour changes, one seam.
"""

import queue


class TerminalIO:
    """Passthrough sink - reproduces the old print()/input() behaviour."""

    def emit(self, level, text):
        print(text)

    def panel(self, kind, text, title=None):
        # ui.py has already rendered the panel (rich or plain) and just hands
        # us finished text for the terminal.
        print(text)

    def prompt(self, text):
        try:
            return input(text)
        except (EOFError, KeyboardInterrupt):
            print()
            return ""

    # TerminalIO is never driven by an external caller.
    def begin_command(self):
        pass

    def end_command(self):
        return []

    @property
    def awaiting_prompt(self):
        return False


class QueueIO:
    """Buffered, cursor-pollable sink for the API/actor thread.

    Every emit/panel/prompt is appended to a shared log with a monotonically
    increasing id, so a web client can poll /events?cursor=N and receive only
    the new lines since its last poll while a command is still running.

    If the game blocks in prompt() (combat sub-loop, conversations, menus),
    the prompt line is logged and the worker thread blocks until the client
    answers via answer_prompt() - the actor model that lets one blocking
    command span many HTTP round-trips.
    """

    def __init__(self):
        self.log = []
        self._id = 0
        self._buf = []
        self._prompt_q = queue.Queue()
        self._awaiting = False
        self._prompt_text = None

    def _append(self, entry):
        self._id += 1
        entry["id"] = self._id
        self.log.append(entry)
        self._buf.append(entry)
        return entry

    def begin_command(self):
        self._buf = []

    def end_command(self):
        return list(self._buf)

    def emit(self, level, text):
        self._append({"level": level, "text": text})

    def panel(self, kind, text, title=None):
        self._append({"level": "panel", "kind": kind, "title": title, "text": text})

    def prompt(self, text):
        self._prompt_text = text
        self._append({"level": "prompt", "text": text})
        self._awaiting = True
        try:
            answer = self._prompt_q.get()
        finally:
            self._awaiting = False
        return answer

    def answer_prompt(self, text):
        self._prompt_q.put(text)

    @property
    def awaiting_prompt(self):
        return self._awaiting

    def poll(self, cursor=0):
        """Return (events_after_cursor, new_cursor)."""
        events = [e for e in self.log if e["id"] > cursor]
        new_cursor = self.log[-1]["id"] if self.log else 0
        return events, new_cursor
