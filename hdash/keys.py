"""Non-blocking keyboard input for Windows and POSIX, normalized to key names.

poll() returns names such as "up", "down", "left", "right", "tab", "shift-tab",
"enter", "esc", "backspace", or the typed character ("q", "k", "?").

- Windows: msvcrt reads console key events. Special keys arrive as a prefix
  ('\\x00' or '\\xe0') followed by a scan code.
- POSIX: the terminal goes into cbreak mode (no line buffering, no echo) and
  select() waits for bytes. Special keys arrive as VT escape sequences.
"""

import os
import sys
import time

if os.name == "nt":
    import msvcrt
else:
    import select
    import termios
    import tty

WIN_SCAN = {"H": "up", "P": "down", "K": "left", "M": "right", "\x0f": "shift-tab",
            "G": "home", "O": "end", "I": "pageup", "Q": "pagedown"}
VT_SEQ = {"[A": "up", "[B": "down", "[C": "right", "[D": "left", "[Z": "shift-tab",
          "OA": "up", "OB": "down", "OC": "right", "OD": "left",
          "[H": "home", "[F": "end", "[5~": "pageup", "[6~": "pagedown"}
PLAIN = {"\t": "tab", "\r": "enter", "\n": "enter", "\x1b": "esc", "\x7f": "backspace", "\x08": "backspace"}


class Keys:
    def __enter__(self):
        self._saved = None
        if os.name != "nt" and sys.stdin.isatty():
            self._saved = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())
        return self

    def __exit__(self, *exc):
        if self._saved is not None:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._saved)

    def poll(self, timeout):
        """Wait up to `timeout` seconds and return the keys pressed meanwhile."""
        return self._poll_windows(timeout) if os.name == "nt" else self._poll_posix(timeout)

    @staticmethod
    def _poll_windows(timeout):
        end = time.monotonic() + timeout
        keys = []
        while True:
            while msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):
                    keys.append(WIN_SCAN.get(msvcrt.getwch(), "unknown"))
                else:
                    keys.append(PLAIN.get(ch, ch))
            if keys or time.monotonic() >= end:
                return keys
            time.sleep(0.01)

    @staticmethod
    def _poll_posix(timeout):
        fd = sys.stdin.fileno()
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            return []
        return parse_vt(os.read(fd, 64).decode("utf-8", "replace"))


def parse_vt(data):
    """Split raw terminal input into key names; a lone ESC is the Esc key."""
    keys, i = [], 0
    while i < len(data):
        ch = data[i]
        if ch == "\x1b" and i + 1 < len(data):
            # Longest known escape sequence starting here.
            for seq in sorted(VT_SEQ, key=len, reverse=True):
                if data.startswith(seq, i + 1):
                    keys.append(VT_SEQ[seq])
                    i += 1 + len(seq)
                    break
            else:
                keys.append("esc")
                i += 1
            continue
        keys.append(PLAIN.get(ch, ch))
        i += 1
    return keys
