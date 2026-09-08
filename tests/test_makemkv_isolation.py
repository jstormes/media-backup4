"""Tests for confining a makemkvcon run to one drive.

No bwrap runs here and no real device is touched: ``/dev`` and ``/sys`` are
injected as temporary directories, so what is asserted is the argv the
sandbox produces, on any machine and with no optical drive present.

The behaviour underneath -- that MakeMKV drops a drive whose sg node it
cannot open, with no SCSI command issued -- was verified by strace against
v1.18.4 and is recorded in :mod:`media_backup.makemkv.isolation`.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from media_backup.config import Config
from media_backup.makemkv import command, isolation

#: An executable that certainly exists, standing in for bwrap. Nothing here
#: runs it; ``prefix`` only checks that it could.
BWRAP = Path("/bin/sh")


class FakeMachine:
    """A ``/dev`` and a ``/sys/class/block`` with the drives we say."""

    def __init__(self, root: Path, drives: dict[str, str], extra_sg=()):
        self.dev = root / "dev"
        self.sys_block = root / "sys" / "class" / "block"
        self.dev.mkdir(parents=True)
        (self.dev / "null").touch()
        for sr, sg in drives.items():
            (self.dev / sg).touch()
            generic = self.sys_block / sr / "device" / "scsi_generic"
            generic.mkdir(parents=True)
            (generic / sg).touch()
        for sg in extra_sg:
            (self.dev / sg).touch()

    def device(self, sr: str) -> str:
        return str(self.dev / sr)

    def kwargs(self) -> dict:
        return {"dev_dir": self.dev, "sys_block": self.sys_block}


class IsolationTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        # Four optical drives and the system disk's sg node, which is the
        # shape of the machine this was written for.
        self.machine = FakeMachine(
            self.root,
            {"sr0": "sg0", "sr1": "sg1", "sr2": "sg2", "sr3": "sg3"},
            extra_sg=["sg4"])
        self.cfg = Config(isolate_drives=True, bwrap=BWRAP)

    def prefix(self, device, cfg=None):
        return isolation.prefix(cfg or self.cfg, device, **self.machine.kwargs())

    def masked(self, sr):
        """The nodes hidden from a run aimed at ``sr``.

        A mask is three arguments: ``--bind /dev/null <node>``.
        """
        argv = self.prefix(self.machine.device(sr))
        return [argv[i + 2] for i, a in enumerate(argv) if a == "--bind"]


class TestSgName(IsolationTestCase):
    def test_reads_the_mapping_from_sysfs(self):
        """Asked, not assumed: sg numbers every SCSI device, sr only the
        optical ones, so the two numberings drift apart."""
        self.assertEqual(
            isolation.sg_name("/dev/sr2", sys_block=self.machine.sys_block), "sg2")

    def test_unknown_device_maps_to_nothing(self):
        self.assertEqual(
            isolation.sg_name("/dev/sr9", sys_block=self.machine.sys_block), "")

    def test_a_device_with_no_generic_node_maps_to_nothing(self):
        (self.machine.sys_block / "sr8").mkdir(parents=True)
        self.assertEqual(
            isolation.sg_name("/dev/sr8", sys_block=self.machine.sys_block), "")


class TestPrefix(IsolationTestCase):
    def test_masks_every_other_generic_node(self):
        """Including sg4, the system disk's. A node MakeMKV was never going
        to open costs nothing to mask, and picking which to leave visible is
        how a drive plugged in since the last scan gets probed anyway."""
        self.assertEqual([Path(node).name for node in self.masked("sr0")],
                         ["sg1", "sg2", "sg3", "sg4"])

    def test_keeps_the_drive_it_was_asked_about(self):
        self.assertNotIn("sg3", [Path(node).name for node in self.masked("sr3")])

    def test_masks_with_dev_null(self):
        """The source of every bind. bwrap mounts binds nodev inside its
        namespace, so opening the masked node fails with EACCES and never
        reaches /dev/null."""
        argv = self.prefix(self.machine.device("sr0"))
        sources = [argv[i + 1] for i, a in enumerate(argv) if a == "--bind"]
        self.assertEqual(set(sources), {"/dev/null"})

    def test_binds_the_real_filesystem(self):
        """Everything else stays: the JRE, the destination, ~/.MakeMKV."""
        argv = self.prefix(self.machine.device("sr0"))
        self.assertEqual(argv[:4], [str(BWRAP), "--dev-bind", "/", "/"])

    def test_ends_with_a_separator(self):
        """So a device path can never be read as a bwrap switch."""
        self.assertEqual(self.prefix(self.machine.device("sr0"))[-1], "--")

    def test_off_by_configuration_wraps_nothing(self):
        cfg = Config(isolate_drives=False, bwrap=BWRAP)
        self.assertEqual(self.prefix(self.machine.device("sr0"), cfg), [])

    def test_no_device_wraps_nothing(self):
        self.assertEqual(self.prefix(""), [])

    def test_unmappable_device_wraps_nothing(self):
        """Better an unisolated run than no run: probing every drive is
        the old behaviour, and a job that will not start is a lost disc."""
        self.assertEqual(self.prefix("/dev/sr9"), [])

    def test_missing_bwrap_wraps_nothing(self):
        cfg = Config(isolate_drives=True, bwrap=Path("/nonexistent/bwrap"))
        self.assertEqual(self.prefix(self.machine.device("sr0"), cfg), [])


class TestProblems(IsolationTestCase):
    """What the operator is told at startup, before a disc is in the drive."""

    def test_a_working_bwrap_is_no_problem(self):
        self.assertEqual(
            isolation.problems(Config(isolate_drives=True, bwrap=Path("/bin/true"))),
            [])

    def test_a_bwrap_that_refuses_is_reported(self):
        """Refused is what a locked-down kernel gives: unprivileged user
        namespaces can be off, or AppArmor can deny them."""
        texts = isolation.problems(
            Config(isolate_drives=True, bwrap=Path("/bin/false")))
        self.assertEqual(len(texts), 1)
        self.assertIn("cannot create a namespace", texts[0])

    def test_a_missing_bwrap_is_reported(self):
        texts = isolation.problems(
            Config(isolate_drives=True, bwrap=Path("/nonexistent/bwrap")))
        self.assertEqual(len(texts), 1)
        self.assertIn("not executable", texts[0])

    def test_every_report_says_what_happens_anyway(self):
        """It is a warning, not a stop: the runs still happen, unisolated."""
        for bwrap in (Path("/bin/false"), Path("/nonexistent/bwrap")):
            texts = isolation.problems(Config(isolate_drives=True, bwrap=bwrap))
            self.assertIn("unisolated", texts[0], bwrap)

    def test_nothing_is_reported_when_isolation_is_off(self):
        self.assertEqual(
            isolation.problems(Config(isolate_drives=False,
                                      bwrap=Path("/nonexistent/bwrap"))),
            [])


class TestCommandsAreWrapped(IsolationTestCase):
    """The sandbox has to reach every command, not only the long one.

    The enumeration is the one that hurts most -- it is the probe that runs
    while another drive is mid-rip -- but a scan and a save open every drive
    just the same.
    """

    def argv(self, builder):
        """Build one command against the fake machine.

        command._prefix calls isolation.prefix with the real /dev and /sys,
        so the injection point has to be the call itself.
        """
        real_prefix = isolation.prefix
        kwargs = self.machine.kwargs()
        patcher = mock.patch.object(
            isolation, "prefix",
            lambda cfg, device: real_prefix(cfg, device, **kwargs))
        patcher.start()
        self.addCleanup(patcher.stop)
        return builder(self.machine.device("sr0"))

    def test_enumeration_is_wrapped(self):
        argv = self.argv(lambda d: command.enumerate_argv(self.cfg, d))
        self.assertEqual(argv[0], str(BWRAP))
        self.assertIn(f"disc:{command.ENUMERATION_INDEX}", argv)

    def test_scan_is_wrapped(self):
        argv = self.argv(lambda d: command.info_argv(self.cfg, 0, d))
        self.assertEqual(argv[0], str(BWRAP))

    def test_save_is_wrapped(self):
        argv = self.argv(
            lambda d: command.mkv_argv(self.cfg, 0, Path("/srv/out"), 1, d))
        self.assertEqual(argv[0], str(BWRAP))

    def test_the_sandbox_wraps_stdbuf_and_not_the_other_way_round(self):
        """stdbuf inside the sandbox, because the sandbox is what execs."""
        argv = self.argv(lambda d: command.mkv_argv(
            Config(isolate_drives=True, bwrap=BWRAP, use_stdbuf=True),
            0, Path("/srv/out"), 1, d))
        self.assertLess(argv.index("--"), argv.index("stdbuf"))
        self.assertLess(argv.index("stdbuf"),
                        next(i for i, a in enumerate(argv)
                             if a.endswith("makemkvcon")))

    def test_an_unwrapped_command_is_unchanged(self):
        """No device, no sandbox -- the argv every existing test asserts."""
        argv = command.mkv_argv(self.cfg, 0, Path("/srv/out"), 1)
        self.assertTrue(argv[0].endswith("makemkvcon") or argv[0] == "stdbuf")


if __name__ == "__main__":
    unittest.main()
