from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from school_csm_control_center.runtime_instance import (
    AlreadyRunningError,
    SingleInstanceGuard,
)
from school_csm_control_center.runtime_paths import (
    migrate_legacy_data,
    resolve_runtime_paths,
)
from school_csm_control_center.storage.survey_store import SurveyStore


def _append_survey_batch(root: str, worker: int, count: int) -> None:
    store = SurveyStore(root)
    for number in range(count):
        store.add_with_next_control_number(
            {
                "mode": "onsite",
                "source_code": "SCC",
                "survey_date": "2026-07-26",
                "meta": {
                    "service_availed": ["Enrollment (Walk-in)"],
                    "worker": worker,
                    "worker_sequence": number,
                },
                "sqd": {f"sqd{index}": 5 for index in range(9)},
            },
            "2026-07-26",
            "123627",
            "SCC",
        )


def _hold_instance_lock(lock_path: str, ready, release) -> None:
    with SingleInstanceGuard(lock_path):
        ready.set()
        release.wait(10)


class RuntimeSafetyTests(unittest.TestCase):
    def test_shared_root_environment_creates_program_specific_directory(self) -> None:
        with TemporaryDirectory() as temporary:
            previous = os.environ.get("MOSS_DATA_ROOT")
            os.environ["MOSS_DATA_ROOT"] = temporary
            try:
                paths = resolve_runtime_paths(Path(temporary) / "install")
            finally:
                if previous is None:
                    os.environ.pop("MOSS_DATA_ROOT", None)
                else:
                    os.environ["MOSS_DATA_ROOT"] = previous
            self.assertEqual(
                paths.data_root,
                (Path(temporary) / "School CSM Control Center").resolve(),
            )

    def test_legacy_migration_is_verified_repeatable_and_never_overwrites_conflicts(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / "install"
            data_root = root / "stable"
            source = install / "data" / "csm_survey" / "surveys.mossjson"
            source.parent.mkdir(parents=True)
            source.write_text('{"records":[{"id":"legacy"}]}', encoding="utf-8")
            paths = resolve_runtime_paths(install, data_root)

            first = migrate_legacy_data(paths)
            destination = paths.csm_data_dir / source.name
            self.assertEqual(first.copied_files, 1)
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertEqual(
                hashlib.sha256(destination.read_bytes()).hexdigest(),
                hashlib.sha256(source.read_bytes()).hexdigest(),
            )

            repeated = migrate_legacy_data(paths)
            self.assertTrue(repeated.already_completed)

            source.write_text('{"records":[{"id":"newer-legacy"}]}', encoding="utf-8")
            before = destination.read_bytes()
            conflict = migrate_legacy_data(paths)
            self.assertIn("data/csm_survey/surveys.mossjson", conflict.conflicts)
            self.assertEqual(destination.read_bytes(), before)
            manifest = json.loads(paths.migration_manifest.read_text(encoding="utf-8"))
            self.assertEqual(
                manifest["files"][0]["action"],
                "conflict_preserved_destination",
            )

    def test_concurrent_processes_produce_unique_control_numbers_without_lost_records(self) -> None:
        with TemporaryDirectory() as temporary:
            context = multiprocessing.get_context("spawn")
            processes = [
                context.Process(target=_append_survey_batch, args=(temporary, worker, 8))
                for worker in range(4)
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(30)
                self.assertEqual(process.exitcode, 0)
            records = SurveyStore(temporary).list()
            controls = [record["control_number"] for record in records]
            self.assertEqual(len(records), 32)
            self.assertEqual(len(set(controls)), 32)
            self.assertEqual(min(controls), "2026-07-0001")
            self.assertEqual(max(controls), "2026-07-0032")

    def test_second_process_is_rejected_while_instance_lock_is_held(self) -> None:
        with TemporaryDirectory() as temporary:
            context = multiprocessing.get_context("spawn")
            ready = context.Event()
            release = context.Event()
            lock_path = str(Path(temporary) / "control-center.lock")
            holder = context.Process(
                target=_hold_instance_lock,
                args=(lock_path, ready, release),
            )
            holder.start()
            self.assertTrue(ready.wait(10))
            try:
                with self.assertRaises(AlreadyRunningError):
                    SingleInstanceGuard(lock_path).acquire()
            finally:
                release.set()
                holder.join(10)
            self.assertEqual(holder.exitcode, 0)


if __name__ == "__main__":
    unittest.main()
