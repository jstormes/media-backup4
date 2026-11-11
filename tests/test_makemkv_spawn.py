"""The real spawn must not hand makemkvcon our stdin.

On some discs makemkvcon reads stdin mid-scan. A GUI launched from a terminal
passed it that tty, the read blocked forever, and every probe died as
"said nothing for 5 minutes" (Dharma & Greg S2, 2026-11-11).
"""

import os
import subprocess
import sys
import unittest

from media_backup.makemkv import runner


class SpawnStdinTest(unittest.TestCase):
    def test_child_reads_eof_not_our_stdin(self):
        # Give *this* process an stdin that is open and silent forever, like
        # an idle terminal: a child that inherited it would block on read().
        r, w = os.pipe()
        saved = os.dup(0)
        os.dup2(r, 0)
        try:
            proc = runner._real_spawn(
                [sys.executable, "-c",
                 "import sys; print(repr(sys.stdin.read()))"])
            out = list(proc.lines())
            self.assertEqual(proc.wait(), 0)
        finally:
            os.dup2(saved, 0)
            for fd in (saved, r, w):
                os.close(fd)
        self.assertEqual([l.strip() for l in out], ["''"])

    def test_forensics_scan_closes_stdin_too(self):
        import inspect
        from media_backup import forensics
        src = inspect.getsource(forensics.scan_disc)
        self.assertIn("stdin=subprocess.DEVNULL", src)


if __name__ == "__main__":
    unittest.main()
