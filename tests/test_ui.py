"""Drawing helpers and key parsing (pure functions, any OS)."""

import os
import unittest

from hdash.keys import parse_vt
from hdash.collect import BRANCH_RE, inside, norm
from hdash.ui import GREEN, RESET, bar, fit, pad, short, visible_len


class Drawing(unittest.TestCase):
    def test_bar_width_is_exact(self):
        for pct in (0, 3, 50, 99.9, 100, 140):
            self.assertEqual(visible_len(bar(pct, 12)), 12)

    def test_fit_keeps_ansi_and_cuts_visible_text(self):
        s = f"{GREEN}hello world{RESET}"
        self.assertEqual(visible_len(fit(s, 5)), 5)
        self.assertTrue(fit(s, 5).startswith(GREEN))

    def test_pad(self):
        self.assertEqual(visible_len(pad(f"{GREEN}ab{RESET}", 6)), 6)

    def test_short(self):
        self.assertEqual(short(934), "934B")
        self.assertEqual(short(41 * 1024), "41K")
        self.assertEqual(short(int(2.5 * 2**30)), "2.5G")


class Keys(unittest.TestCase):
    def test_arrows_tab_and_esc(self):
        self.assertEqual(parse_vt("\x1b[A\x1b[B\t\x1b[Zq\r\x1b"), ["up", "down", "tab", "shift-tab", "q", "enter", "esc"])

    def test_application_mode_arrows(self):
        self.assertEqual(parse_vt("\x1bOA\x1bOD"), ["up", "left"])


class Paths(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Herdr only reports \\\\?\\ paths on Windows")
    def test_windows_long_path_prefix(self):
        self.assertEqual(norm("\\\\?\\C:\\Users\\x\\"), norm("C:/Users/x"))

    def test_inside(self):
        self.assertTrue(inside("/home/a/repo/src", "/home/a/repo"))
        self.assertFalse(inside("/home/a/repo2", "/home/a/repo"))

    def test_branch_line(self):
        m = BRANCH_RE.match("## feat/x...origin/feat/x [ahead 2, behind 1]")
        self.assertEqual((m.group("branch"), m.group("track")), ("feat/x", "ahead 2, behind 1"))
        self.assertEqual(BRANCH_RE.match("## main").group("branch"), "main")


if __name__ == "__main__":
    unittest.main()
