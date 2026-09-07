"""On-disk persistence for collections.

Layout under ``media_path``::

    collections/<collection-uuid>/
        collection.json          (+ .bak)
        discs/<disc-uuid>/
            data/                BDMV/... or VIDEO_TS/...
            logs/attempt-1.log
            rejected/attempt-1/
    finished/<collection-uuid>/.complete
    cancelled/<collection-uuid>/

One JSON per collection with the discs nested inside, rather than a file per
disc: one atomic write per state change, no window in which two files
disagree. Safe because the GUI thread is the only writer.

Finishing and cancelling are ``os.rename``, which is atomic and instant even
for a 200 GB collection -- but only within one filesystem, which
:func:`config.validate` checks at startup.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from . import model
from .config import Config

logger = logging.getLogger(__name__)

COLLECTION_FILE = "collection.json"
BACKUP_FILE = "collection.json.bak"
TEMP_FILE = "collection.json.tmp"
COMPLETE_MARKER = ".complete"


class StoreError(Exception):
    """A filesystem operation failed in a way the operator must be told about."""


class CollectionStore:
    """Reads and writes collections beneath ``config.media_path``."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    # -- paths --------------------------------------------------------------

    def collection_dir(self, collection: model.Collection) -> Path:
        return self.cfg.collections_path / collection.collection_id

    def disc_dir(self, collection: model.Collection, disc: model.Disc) -> Path:
        return self.collection_dir(collection) / "discs" / disc.disc_id

    def data_dir(self, collection: model.Collection, disc: model.Disc) -> Path:
        return self.disc_dir(collection, disc) / "data"

    def log_path(self, collection, disc, attempt: int) -> Path:
        return self.disc_dir(collection, disc) / "logs" / f"attempt-{attempt}.log"

    def reject_dir(self, collection, disc, attempt: int) -> Path:
        return self.disc_dir(collection, disc) / "rejected" / f"attempt-{attempt}"

    def relative(self, collection: model.Collection, path: Path) -> str:
        """Path relative to the collection dir, for storing in the JSON."""
        try:
            return str(path.relative_to(self.collection_dir(collection)))
        except ValueError:
            return str(path)

    # -- reading ------------------------------------------------------------

    def load_all(self) -> list[model.Collection]:
        """Load every open collection. Unreadable ones are skipped, not fatal."""
        root = self.cfg.collections_path
        if not root.is_dir():
            return []
        out = []
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            collection = self._load_one(child)
            if collection is not None:
                out.append(collection)
        out.sort(key=lambda c: c.created_at)
        return out

    def _load_one(self, directory: Path) -> model.Collection | None:
        for name in (COLLECTION_FILE, BACKUP_FILE):
            path = directory / name
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("%s is unreadable (%s); trying the backup", path, exc)
                continue
            if name == BACKUP_FILE:
                logger.warning("recovered %s from its backup copy", directory.name)
            try:
                return model.Collection.from_dict(data)
            except (TypeError, ValueError) as exc:
                logger.warning("%s is not a valid collection (%s)", path, exc)
        logger.error("no readable collection.json in %s", directory)
        return None

    # -- writing ------------------------------------------------------------

    def save(self, collection: model.Collection) -> None:
        """Write the collection atomically. Never leaves a partial file."""
        collection.updated_at = model.now()
        directory = self.collection_dir(collection)
        directory.mkdir(parents=True, exist_ok=True)

        target = directory / COLLECTION_FILE
        tmp = directory / TEMP_FILE
        payload = json.dumps(collection.to_dict(), indent=2, sort_keys=True)

        try:
            with tmp.open("w") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if target.exists():
                os.replace(target, directory / BACKUP_FILE)
            os.replace(tmp, target)
            self._fsync_dir(directory)
        except OSError as exc:
            raise StoreError(f"could not save {target}: {exc}") from exc

    @staticmethod
    def _fsync_dir(directory: Path) -> None:
        """Make the rename itself durable, not just the file contents."""
        try:
            fd = os.open(directory, os.O_DIRECTORY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    # -- lifecycle ----------------------------------------------------------

    def create(self, identifier: str = "", app_version: str = "") -> model.Collection:
        collection = model.Collection(identifier=identifier, app_version=app_version)
        self.collection_dir(collection).mkdir(parents=True, exist_ok=True)
        self.save(collection)
        return collection

    def add_disc(self, collection: model.Collection, drive) -> model.Disc:
        """Add the disc currently in ``drive`` (a :class:`DriveState`)."""
        disc = model.Disc(
            ordinal=collection.next_ordinal(),
            label=getattr(drive, "label", ""),
            media=getattr(drive, "media", ""),
            disc_size_bytes=getattr(drive, "size", 0),
        )
        collection.discs.append(disc)
        self.disc_dir(collection, disc).mkdir(parents=True, exist_ok=True)
        self.save(collection)
        return disc

    def prepare_attempt(self, collection: model.Collection, disc: model.Disc) -> tuple[Path, Path, int]:
        """Clear the way for a new attempt; return (data_dir, log_path, n).

        makemkvcon refuses to write into a directory that already contains a
        backup (MSG:5068), so any previous partial output has to be moved out
        of the way here. It is moved rather than deleted -- a half-copied disc
        is evidence about why it failed.

        Moving lazily, at retry time rather than at failure time, means an
        operator who never retries still finds the partial under ``data/``
        where they would look for it.
        """
        attempt = disc.attempt_count + 1
        data = self.data_dir(collection, disc)
        log = self.log_path(collection, disc, attempt)
        log.parent.mkdir(parents=True, exist_ok=True)

        if data.exists() and any(data.iterdir()):
            reject = self.reject_dir(collection, disc, attempt - 1 or 1)
            reject.parent.mkdir(parents=True, exist_ok=True)
            if reject.exists():
                shutil.rmtree(reject, ignore_errors=True)
            try:
                os.replace(data, reject)
            except OSError as exc:
                raise StoreError(
                    f"could not move the previous partial copy aside: {exc}") from exc
            if disc.attempts:
                disc.attempts[-1].rejected_path = self.relative(collection, reject)
            self._prune_rejected(collection, disc)

        data.mkdir(parents=True, exist_ok=True)
        return data, log, attempt

    def _prune_rejected(self, collection: model.Collection, disc: model.Disc) -> None:
        """Keep only the most recent failed attempts' data.

        Three failed Blu-ray attempts is 100+ GB. Logs are never pruned; they
        are small and they are what diagnosing a bad disc actually needs.
        """
        keep = max(0, self.cfg.keep_rejected_attempts)
        root = self.disc_dir(collection, disc) / "rejected"
        if not root.is_dir():
            return
        existing = sorted(
            (p for p in root.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
        )
        for stale in existing[:max(0, len(existing) - keep)]:
            logger.info("pruning old rejected attempt %s", stale)
            shutil.rmtree(stale, ignore_errors=True)

    def rejected_bytes(self, collection: model.Collection) -> int:
        """Total size of retained failed attempts, for display."""
        from .makemkv.inspect import tree_size
        total = 0
        for disc in collection.discs:
            total += tree_size(self.disc_dir(collection, disc) / "rejected")
        return total

    def finish(self, collection: model.Collection) -> Path:
        """Move a finished collection into ``finished/`` by rename."""
        if collection.has_active_jobs:
            raise StoreError("a disc is still being copied")
        collection.state = model.FINISHED
        collection.finished_at = model.now()
        self.save(collection)
        return self._move_to(collection, self.cfg.finished_dir, marker=True)

    def cancel(self, collection: model.Collection) -> Path:
        """Move a cancelled collection aside. Deliberately not a delete."""
        if collection.has_active_jobs:
            raise StoreError("a disc is still being copied")
        collection.state = model.CANCELLED
        self.save(collection)
        return self._move_to(collection, self.cfg.cancelled_dir, marker=False)

    def _move_to(self, collection: model.Collection, parent: Path, marker: bool) -> Path:
        parent.mkdir(parents=True, exist_ok=True)
        source = self.collection_dir(collection)
        target = parent / collection.collection_id
        if target.exists():
            raise StoreError(f"{target} already exists")
        try:
            os.replace(source, target)
        except OSError as exc:
            raise StoreError(
                f"could not move {source} to {target}: {exc}. Finished and "
                "cancelled directories must be on the same filesystem as "
                "media_path.") from exc
        if marker:
            # Written last, so an external archival process can tell a fully
            # written collection from one still being moved.
            try:
                (target / COMPLETE_MARKER).write_text(model.now())
            except OSError:
                logger.warning("could not write the .complete marker in %s", target)
        self._fsync_dir(parent)
        return target

    # -- restart recovery ---------------------------------------------------

    def recover_interrupted(self, collection: model.Collection) -> list[model.Disc]:
        """Mark discs that were mid-copy when the app died.

        makemkvcon cannot resume, and it will not write into a directory that
        already holds a partial backup, so an interrupted copy is dead. Say so
        plainly and offer a retry rather than pretending it might continue.
        """
        recovered = []
        for disc in collection.discs:
            if not disc.is_active:
                continue
            disc.state = model.FAILED
            disc.state_detail = "interrupted"
            disc.updated_at = model.now()
            attempt = disc.last_attempt
            if attempt is None or attempt.ended_at:
                attempt = model.Attempt(attempt=disc.attempt_count + 1)
                disc.attempts.append(attempt)
            attempt.ended_at = model.now()
            attempt.outcome = "failure"
            attempt.error_kind = model.ERR_INTERRUPTED
            attempt.failure_reason = (
                "The application stopped while this disc was being copied. "
                "MakeMKV cannot resume a backup, so it has to be started again.")
            recovered.append(disc)
        if recovered:
            self.save(collection)
        return recovered
