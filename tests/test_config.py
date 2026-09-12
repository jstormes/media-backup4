"""Tests for configuration loading and environment validation."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from media_backup import config


class TestImdbLookupSettings(unittest.TestCase):
    """Credentials for the IMDb mirror come from the environment.

    imdb.com cannot be read by a program -- CloudFront answers a non-browser
    user-agent with 403 and a browser one with an empty 202, measured
    2026-09-11 -- so publishing looks titles up in a local mirror instead.
    """

    def test_nothing_is_configured_by_default(self):
        """A checkout carries no credential, so a fresh clone has none."""
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = config.load(Path("/nonexistent/config.json"))
        self.assertEqual(cfg.imdb_user, "")
        self.assertEqual(cfg.imdb_password, "")
        self.assertFalse(cfg.imdb_configured)

    def test_the_environment_supplies_them(self):
        env = {"MEDIA_BACKUP_IMDB_USER": "imdb",
               "MEDIA_BACKUP_IMDB_PASSWORD": "secret",
               "MEDIA_BACKUP_IMDB_HOST": "nas2"}
        with mock.patch.dict(os.environ, env, clear=True):
            cfg = config.load(Path("/nonexistent/config.json"))
        self.assertEqual(cfg.imdb_user, "imdb")
        self.assertEqual(cfg.imdb_password, "secret")
        self.assertTrue(cfg.imdb_configured)

    def test_the_environment_beats_the_config_file(self):
        """A password set deliberately must not lose to a stale file."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"imdb_user": "from_file",
                                        "imdb_host": "old-host"}))
            with mock.patch.dict(os.environ,
                                 {"MEDIA_BACKUP_IMDB_USER": "from_env"},
                                 clear=True):
                cfg = config.load(path)
        self.assertEqual(cfg.imdb_user, "from_env")
        self.assertEqual(cfg.imdb_host, "old-host", "unset vars leave the file alone")

    def test_an_empty_variable_is_not_a_value(self):
        """Exporting an empty string is how a variable gets unset in practice."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"imdb_user": "from_file"}))
            with mock.patch.dict(os.environ, {"MEDIA_BACKUP_IMDB_USER": ""},
                                 clear=True):
                cfg = config.load(path)
        self.assertEqual(cfg.imdb_user, "from_file")

    def test_an_unparseable_port_does_not_crash_startup(self):
        """load() runs before the GUI can show anything, so it must not raise."""
        with mock.patch.dict(os.environ, {"MEDIA_BACKUP_IMDB_PORT": "not-a-number"},
                             clear=True):
            cfg = config.load(Path("/nonexistent/config.json"))
        self.assertEqual(cfg.imdb_port, 3306, "falls back to the default")

    def test_the_password_is_redacted_from_a_dump(self):
        """to_dict() feeds logging and display; nothing round-trips through it."""
        cfg = config.Config(imdb_password="secret")
        self.assertEqual(cfg.to_dict()["imdb_password"], "***")
        self.assertEqual(cfg.imdb_password, "secret", "the real value still works")

    def test_an_unset_password_redacts_to_empty_not_to_stars(self):
        """A redacted marker for an unset password would read as
        "one is configured", which is the opposite of the truth."""
        self.assertEqual(config.Config().to_dict()["imdb_password"], "")

    def test_a_host_without_a_user_is_a_warning_not_an_error(self):
        cfg = config.Config(imdb_host="nas2", imdb_user="")
        hits = [p for p in config.validate(cfg) if "IMDb" in p.text]
        self.assertTrue(hits, "the operator should be told lookups are off")
        self.assertFalse(any(p.is_fatal for p in hits), "publishing still works")


class TestForensicsPath(unittest.TestCase):
    """Where captured disc navigation data is kept."""

    def test_it_defaults_beside_the_other_output(self):
        cfg = config.Config(media_path=Path("/srv/media-backup"))
        self.assertEqual(cfg.forensics_dir,
                         Path("/srv/media-backup/forensics"))

    def test_it_can_live_on_another_filesystem(self):
        """Unlike finished/ and cancelled/, nothing is renamed into it.

        And it arguably should live elsewhere: a collection can be rebuilt by
        re-ripping the disc, while a confirmed answer in truth.json is
        somebody's afternoon and cannot be re-derived.
        """
        cfg = config.Config(
            media_path=Path("/srv/media-backup"),
            forensics_path=Path("/nas/forensics"))
        self.assertEqual(cfg.forensics_dir, Path("/nas/forensics"))

    def test_an_explicit_null_falls_back_to_the_default(self):
        """A null in config.json used to raise Path(None)."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"forensics_path": None}))
            self.assertEqual(config.load(path).forensics_dir,
                             Path("/srv/media-backup/forensics"))


class TestLoad(unittest.TestCase):
    def test_missing_file_yields_defaults(self):
        cfg = config.load(Path("/nonexistent/config.json"))
        self.assertEqual(cfg.cache_mb, 1024)
        self.assertTrue(cfg.decrypt)

    def test_corrupt_file_yields_defaults_rather_than_crashing(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text("{not json at all")
            self.assertEqual(config.load(p).cache_mb, 1024)

    def test_values_override_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text(json.dumps({"cache_mb": 64, "decrypt": False,
                                     "media_path": "/mnt/discs"}))
            cfg = config.load(p)
            self.assertEqual(cfg.cache_mb, 64)
            self.assertFalse(cfg.decrypt)
            self.assertEqual(cfg.media_path, Path("/mnt/discs"))

    def test_unknown_keys_are_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text(json.dumps({"cache_mb": 8, "from_a_future_version": 1}))
            self.assertEqual(config.load(p).cache_mb, 8)

    def test_env_var_selects_the_path(self):
        with mock.patch.dict(os.environ, {config.ENV_VAR: "/tmp/elsewhere.json"}):
            self.assertEqual(config.default_path(), Path("/tmp/elsewhere.json"))

    def test_example_config_is_loadable_and_complete(self):
        """config.example.json is what an operator copies; it must work."""
        example = Path(__file__).resolve().parent.parent / "config.example.json"
        cfg = config.load(example)
        self.assertEqual(cfg.media_path, Path("/srv/media-backup"))
        known = set(cfg.to_dict())
        for key in json.loads(example.read_text()):
            self.assertIn(key, known, f"{key} in example is not a real setting")


class TestDerivedPaths(unittest.TestCase):
    def test_defaults_sit_inside_media_path(self):
        cfg = config.Config(media_path=Path("/srv/x"))
        self.assertEqual(cfg.finished_dir, Path("/srv/x/finished"))
        self.assertEqual(cfg.cancelled_dir, Path("/srv/x/cancelled"))
        self.assertEqual(cfg.collections_path, Path("/srv/x/collections"))

    def test_explicit_paths_win(self):
        cfg = config.Config(media_path=Path("/srv/x"), finished_path=Path("/srv/y"))
        self.assertEqual(cfg.finished_dir, Path("/srv/y"))


class TestValidate(unittest.TestCase):
    def test_missing_media_path_is_fatal_and_explains_itself(self):
        problems = config.validate(config.Config(media_path=Path("/nope/nowhere")))
        self.assertTrue(problems[0].is_fatal)
        self.assertIn("mount", problems[0].text)

    def test_a_usable_setup_has_no_fatal_problems(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = config.Config(media_path=Path(d), makemkvcon=Path("/bin/sh"))
            fatal = [p for p in config.validate(cfg) if p.is_fatal]
            self.assertEqual(fatal, [], [p.text for p in fatal])

    def test_missing_makemkvcon_is_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = config.Config(media_path=Path(d),
                                makemkvcon=Path("/nonexistent/makemkvcon"))
            self.assertTrue(any("makemkvcon" in p.text and p.is_fatal
                                for p in config.validate(cfg)))

    def test_cross_filesystem_finished_path_is_fatal(self):
        """The whole finish-by-rename design depends on one filesystem."""
        with tempfile.TemporaryDirectory() as d:
            cfg = config.Config(media_path=Path(d), makemkvcon=Path("/bin/sh"),
                                finished_path=Path(d) / "finished")
            cfg.finished_dir.mkdir()   # must exist to be stat'ed in its own right
            real_stat = Path.stat

            def fake_stat(self, *a, **k):
                st = real_stat(self, *a, **k)
                if "finished" in str(self):
                    return os.stat_result((st.st_mode, st.st_ino, st.st_dev + 1)
                                          + tuple(st)[3:])
                return st

            with mock.patch.object(Path, "stat", fake_stat):
                problems = config.validate(cfg)
            self.assertTrue(any("different filesystem" in p.text and p.is_fatal
                                for p in problems))

    def test_absent_finished_path_is_checked_against_its_parent(self):
        """Before first run finished/ does not exist; its parent stands in."""
        with tempfile.TemporaryDirectory() as d:
            cfg = config.Config(media_path=Path(d), makemkvcon=Path("/bin/sh"))
            self.assertFalse(cfg.finished_dir.exists())
            fatal = [p for p in config.validate(cfg) if p.is_fatal]
            self.assertEqual(fatal, [], [p.text for p in fatal])

    def test_an_unusable_sandbox_warns_but_does_not_stop_the_run(self):
        """Probing every drive is slow; not starting is a disc not backed up."""
        with tempfile.TemporaryDirectory() as d:
            cfg = config.Config(media_path=Path(d), makemkvcon=Path("/bin/sh"),
                                isolate_drives=True,
                                bwrap=Path("/nonexistent/bwrap"))
            problems = config.validate(cfg)
            self.assertTrue(any("bwrap" in p.text and not p.is_fatal
                                for p in problems))
            self.assertEqual([p for p in problems if p.is_fatal], [])

    def test_no_sandbox_warning_when_isolation_is_off(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = config.Config(media_path=Path(d), makemkvcon=Path("/bin/sh"),
                                isolate_drives=False,
                                bwrap=Path("/nonexistent/bwrap"))
            self.assertFalse(any("bwrap" in p.text for p in config.validate(cfg)))

    def test_nonsense_thresholds_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = config.Config(media_path=Path(d), makemkvcon=Path("/bin/sh"),
                                size_ratio_floor=1.7, cache_mb=0)
            texts = " ".join(p.text for p in config.validate(cfg) if p.is_fatal)
            self.assertIn("size_ratio_floor", texts)
            self.assertIn("cache_mb", texts)


class TestFreeSpace(unittest.TestCase):
    def test_has_room_accounts_for_the_margin(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = config.Config(media_path=Path(d), min_free_margin_bytes=0)
            with mock.patch("media_backup.config.free_bytes", return_value=1000):
                self.assertTrue(config.has_room_for(cfg, 900))
                self.assertFalse(config.has_room_for(cfg, 960))  # 960*1.05 > 1000

    def test_margin_is_required_on_top_of_the_disc(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = config.Config(media_path=Path(d), min_free_margin_bytes=500)
            with mock.patch("media_backup.config.free_bytes", return_value=1000):
                self.assertFalse(config.has_room_for(cfg, 600))

    def test_unreadable_path_reports_no_space_rather_than_raising(self):
        cfg = config.Config(media_path=Path("/nonexistent"))
        self.assertEqual(config.free_bytes(cfg), 0)


class TestEnsureDirectories(unittest.TestCase):
    def test_creates_the_standard_tree(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = config.Config(media_path=Path(d))
            config.ensure_directories(cfg)
            for p in (cfg.collections_path, cfg.finished_dir, cfg.cancelled_dir):
                self.assertTrue(p.is_dir(), p)
            config.ensure_directories(cfg)  # idempotent


if __name__ == "__main__":
    unittest.main()
