"""Storage contract tests. Every file operation is isolated in a temporary directory."""

import copy
import csv
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import storage


def make_task(task_id: str = "task-1", **changes: object) -> storage.Task:
    task: storage.Task = {
        "id": task_id,
        "title": "课程项目",
        "description": "",
        "priority": "medium",
        "completed": False,
        "category_id": None,
        "planned_start": None,
        "due_at": None,
        "progress_mode": "manual",
        "manual_progress": 0,
        "created_at": "2026-10-17T12:30:00",
        "completed_at": None,
    }
    task.update(changes)
    return task


def csv_stream(tasks: list[storage.Task], *, fields=None) -> io.StringIO:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields or storage.CSV_FIELDS)
    writer.writeheader()
    for task in tasks:
        row = {key: "" if value is None else str(value) for key, value in task.items()}
        row["completed"] = str(task["completed"]).lower()
        writer.writerow(row)
    stream.seek(0)
    return stream


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "data" / "tasks.csv"

    def assert_roundtrip(self, tasks):
        original = copy.deepcopy(tasks)
        storage.save_tasks(tasks, self.path)
        self.assertEqual(storage.load_tasks(self.path), tasks)
        self.assertEqual(tasks, original)

    def assert_rejected_save(self, tasks, error=ValueError):
        storage.save_tasks([make_task()], self.path)
        before = self.path.read_bytes()
        with self.assertRaises(error):
            storage.save_tasks(tasks, self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob(".cleartask-*")), [])

    def test_initialize_creates_header_and_parents(self):
        storage.initialize_storage(self.path)
        with self.path.open(encoding="utf-8", newline="") as stream:
            self.assertEqual(list(csv.reader(stream)), [list(storage.CSV_FIELDS)])

    def test_repeated_initialize_keeps_bytes(self):
        storage.save_tasks([make_task()], self.path)
        before = self.path.read_bytes()
        storage.initialize_storage(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_initialize_does_not_repair_corruption(self):
        self.path.parent.mkdir()
        self.path.write_text("wrong,header\n", encoding="utf-8")
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            storage.initialize_storage(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_load_missing_initializes(self):
        self.assertEqual(storage.load_tasks(self.path), [])
        self.assertTrue(self.path.is_file())

    def test_empty_roundtrip(self):
        self.assert_roundtrip([])

    def test_single_roundtrip(self):
        self.assert_roundtrip([make_task()])

    def test_multiple_roundtrip_and_full_replacement(self):
        self.assert_roundtrip([make_task("a"), make_task("b")])
        self.assert_roundtrip([make_task("c")])

    def test_boolean_conversion_and_canonical_output(self):
        stream = csv_stream([make_task("a", completed=True), make_task("b")])
        mixed_case = io.StringIO(stream.getvalue().replace("true", "TrUe").replace("false", "FALSE"))
        storage.import_tasks(mixed_case, self.path)
        tasks = storage.load_tasks(self.path)
        self.assertIs(tasks[0]["completed"], True)
        self.assertIs(tasks[1]["completed"], False)
        with self.path.open(encoding="utf-8", newline="") as saved:
            self.assertEqual([row["completed"] for row in csv.DictReader(saved)], ["true", "false"])

    def test_invalid_csv_boolean(self):
        for value in ("1", "yes", "", " false "):
            with self.subTest(value=value), self.assertRaises(ValueError):
                storage.import_tasks(io.StringIO(csv_stream([make_task()]).getvalue().replace("false", value)), self.path)
        self.assertFalse(self.path.exists())

    def test_progress_integer_conversion_and_bounds(self):
        self.assert_roundtrip([make_task("a", manual_progress=0), make_task("b", manual_progress=100)])
        self.assertIs(type(storage.load_tasks(self.path)[0]["manual_progress"]), int)
        for value, error in ((-1, ValueError), (101, ValueError), (True, TypeError), (1.5, TypeError), ("10", TypeError)):
            with self.subTest(value=value):
                self.assert_rejected_save([make_task(manual_progress=value)], error)

    def test_csv_progress_rejects_nonintegers(self):
        for value in ("1.5", "True", "", "1e2"):
            stream = csv_stream([make_task(manual_progress=value)])
            with self.subTest(value=value), self.assertRaises(ValueError):
                storage.import_tasks(stream, self.path)

    def test_progress_modes_preserve_manual_value(self):
        self.assert_roundtrip([
            make_task("a", progress_mode="subtasks", manual_progress=37),
            make_task("b", progress_mode="manual", completed=True, manual_progress=12),
        ])
        self.assert_rejected_save([make_task(progress_mode="automatic")])

    def test_optional_fields_empty(self):
        self.assert_roundtrip([make_task()])
        task = storage.load_tasks(self.path)[0]
        for field in ("category_id", "planned_start", "due_at", "completed_at"):
            self.assertIsNone(task[field])

    def test_valid_dates_and_datetimes(self):
        self.assert_roundtrip([
            make_task("a", planned_start="2028-02-29", due_at="2028-03-01T00:00:00"),
            make_task("b", planned_start="2026-10-17T09:10:11", due_at="2026-10-18"),
            make_task("c", completed=True, completed_at="2026-10-17T23:59:59"),
            make_task("d", planned_start="2026-10-17"),
            make_task("e", due_at="2026-10-18"),
        ])

    def test_invalid_dates_formats_and_types(self):
        for field in ("planned_start", "due_at", "created_at", "completed_at"):
            for value in ("2026-02-30", "2026-10-17T25:00:00", "2026-1-02", "", "2026-10-17T12:00:00Z", "2026-10-17T12:00:00.123", "2026-10-17 12:00:00", "2026-10-17T12:00:00+08:00"):
                with self.subTest(field=field, value=value):
                    self.assert_rejected_save([make_task(completed=True, **{field: value})])
        self.assert_rejected_save([make_task(due_at=123)], TypeError)

    def test_required_datetime_cannot_be_date_or_none(self):
        self.assert_rejected_save([make_task(created_at="2026-10-17")])
        self.assert_rejected_save([make_task(created_at=None)], TypeError)
        self.assert_rejected_save([make_task(completed=True, completed_at="2026-10-17")])

    def test_completed_at_state_consistency(self):
        self.assert_rejected_save([make_task(completed_at="2026-10-17T12:00:00")])
        self.assert_roundtrip([make_task(completed=True, completed_at=None)])

    def test_blank_id_and_title(self):
        for field in ("id", "title"):
            for value in ("", " \t\n"):
                with self.subTest(field=field, value=value):
                    self.assert_rejected_save([make_task(**{field: value})])

    def test_title_whitespace_is_not_silently_changed(self):
        self.assert_roundtrip([make_task(title="  保留空白  ")])

    def test_invalid_priority(self):
        self.assert_rejected_save([make_task(priority="urgent")])

    def test_duplicate_ids(self):
        self.assert_rejected_save([make_task(), make_task()])

    def test_top_level_and_task_types(self):
        for tasks in (None, {}, (), "tasks", [None], [[]]):
            with self.subTest(tasks=tasks):
                self.assert_rejected_save(tasks, TypeError)

    def test_required_python_field_types(self):
        for field, value in (("id", 1), ("title", None), ("description", None), ("priority", []), ("progress_mode", []), ("completed", 1), ("category_id", 3)):
            with self.subTest(field=field):
                self.assert_rejected_save([make_task(**{field: value})], TypeError)

    def test_missing_and_unknown_task_fields(self):
        task = make_task()
        del task["description"]
        self.assert_rejected_save([task])
        self.assert_rejected_save([make_task(unexpected="do not discard")])

    def test_invalid_csv_headers(self):
        headers = [list(storage.CSV_FIELDS[:-1]), list(storage.CSV_FIELDS) + ["extra"], list(storage.CSV_FIELDS) + ["id"], list(reversed(storage.CSV_FIELDS))]
        for header in headers:
            stream = io.StringIO()
            csv.writer(stream).writerow(header)
            stream.seek(0)
            with self.subTest(header=header), self.assertRaises(ValueError):
                storage.import_tasks(stream, self.path)
        self.assertFalse(self.path.exists())

    def test_empty_or_malformed_csv(self):
        header = csv_stream([]).getvalue()
        for content in ("", "\n", header + '"unterminated', header + "too,few\n", header + ",".join(["x"] * 13) + "\n"):
            with self.subTest(content=content), self.assertRaises(ValueError):
                storage.import_tasks(io.StringIO(content), self.path)

    def test_load_corruption_raises_and_keeps_bytes(self):
        self.path.parent.mkdir()
        self.path.write_bytes(b"corrupt\n")
        with self.assertRaises(ValueError):
            storage.load_tasks(self.path)
        self.assertEqual(self.path.read_bytes(), b"corrupt\n")

    def test_invalid_utf8_propagates_without_replacing_target(self):
        storage.save_tasks([make_task("old")], self.path)
        before = self.path.read_bytes()
        source = self.root / "invalid-encoding.csv"
        source.write_bytes(b"\xff\xfeinvalid UTF-8")
        with self.assertRaises(UnicodeDecodeError):
            storage.import_tasks(source, self.path, mode="replace")
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(UnicodeDecodeError):
            storage.load_tasks(source)
        self.assertEqual(source.read_bytes(), b"\xff\xfeinvalid UTF-8")

    def test_csv_quotes_commas_newlines_and_chinese(self):
        self.assert_roundtrip([make_task(title='复习, "Python"\n第二行', description='中文描述\r\n含逗号,和"引号"', category_id="课程,一")])

    def test_bom_path_and_stream(self):
        content = "\ufeff" + csv_stream([make_task()]).getvalue()
        source = self.root / "bom.csv"
        source.write_text(content, encoding="utf-8", newline="")
        storage.import_tasks(source, self.path)
        self.assertEqual(storage.load_tasks(self.path), [make_task()])
        storage.import_tasks(io.StringIO(content), self.path, mode="replace")
        self.assertFalse(self.path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_merge_import(self):
        storage.save_tasks([make_task("old")], self.path)
        stream = csv_stream([make_task("new")])
        self.assertEqual(storage.import_tasks(stream, self.path), 1)
        self.assertEqual(storage.load_tasks(self.path), [make_task("old"), make_task("new")])
        self.assertFalse(stream.closed)

    def test_merge_collision_rejects_entire_batch(self):
        storage.save_tasks([make_task("old")], self.path)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "duplicate ID"):
            storage.import_tasks(csv_stream([make_task("new"), make_task("old")]), self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_internal_import_duplicates_rejected_in_both_modes(self):
        storage.save_tasks([make_task("old")], self.path)
        before = self.path.read_bytes()
        for mode in ("merge", "replace"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                storage.import_tasks(csv_stream([make_task(), make_task()]), self.path, mode=mode)
            self.assertEqual(self.path.read_bytes(), before)

    def test_replace_import_and_empty_replacement(self):
        storage.save_tasks([make_task("old")], self.path)
        self.assertEqual(storage.import_tasks(csv_stream([make_task("new")]), self.path, mode="replace"), 1)
        self.assertEqual(storage.load_tasks(self.path), [make_task("new")])
        self.assertEqual(storage.import_tasks(csv_stream([]), self.path, mode="replace"), 0)
        self.assertEqual(storage.load_tasks(self.path), [])

    def test_invalid_source_keeps_target_for_both_modes(self):
        storage.save_tasks([make_task("old")], self.path)
        before = self.path.read_bytes()
        for mode in ("merge", "replace"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                storage.import_tasks(csv_stream([make_task("good"), make_task("bad", priority="bad")]), self.path, mode=mode)
            self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_import_does_not_create_missing_target(self):
        with self.assertRaises(ValueError):
            storage.import_tasks(io.StringIO("broken"), self.path)
        self.assertFalse(self.path.exists())

    def test_missing_import_source_is_not_created(self):
        source = self.root / "missing.csv"
        with self.assertRaises(FileNotFoundError):
            storage.import_tasks(source, self.path)
        self.assertFalse(source.exists())
        self.assertFalse(self.path.exists())

    def test_invalid_import_mode(self):
        with self.assertRaises(ValueError):
            storage.import_tasks(csv_stream([]), self.path, mode="append")
        self.assertFalse(self.path.exists())

    def test_merge_rejects_corrupt_target(self):
        self.path.parent.mkdir()
        self.path.write_text("broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            storage.import_tasks(csv_stream([make_task()]), self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "broken")

    def test_export_stream_roundtrip_and_ownership(self):
        storage.save_tasks([make_task()], self.path)
        before = self.path.read_bytes()
        stream = io.StringIO(newline="")
        storage.export_tasks(stream, self.path)
        self.assertFalse(stream.closed)
        stream.seek(0)
        other = self.root / "imported.csv"
        storage.import_tasks(stream, other)
        self.assertEqual(storage.load_tasks(other), [make_task()])
        self.assertEqual(self.path.read_bytes(), before)

    def test_export_path_and_same_path_rejection(self):
        storage.save_tasks([make_task()], self.path)
        destination = self.root / "exports" / "out.csv"
        storage.export_tasks(destination, self.path)
        self.assertEqual(storage.load_tasks(destination), [make_task()])
        with self.assertRaises(ValueError):
            storage.export_tasks(self.path.parent / "." / self.path.name, self.path)

    def test_export_hardlink_alias_rejected(self):
        storage.save_tasks([make_task()], self.path)
        alias = self.root / "alias.csv"
        os.link(self.path, alias)
        with self.assertRaises(ValueError):
            storage.export_tasks(alias, self.path)

    def test_export_missing_source_does_not_create_it(self):
        with self.assertRaises(FileNotFoundError):
            storage.export_tasks(io.StringIO(), self.path)
        self.assertFalse(self.path.exists())

    def test_export_stream_failure_propagates(self):
        storage.save_tasks([make_task()], self.path)
        stream = io.StringIO()
        stream.close()
        with self.assertRaises(ValueError):
            storage.export_tasks(stream, self.path)

    def test_atomic_replace_failure_keeps_old_file_and_cleans_temp(self):
        storage.save_tasks([make_task("old")], self.path)
        before = self.path.read_bytes()
        with patch("storage.os.replace", side_effect=OSError("simulated replace failure")):
            with self.assertRaises(OSError):
                storage.save_tasks([make_task("new")], self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob(".cleartask-*")), [])

    def test_write_failure_keeps_old_file_and_cleans_temp(self):
        storage.save_tasks([make_task("old")], self.path)
        before = self.path.read_bytes()
        with patch("storage._write_stream", side_effect=OSError("simulated disk failure")):
            with self.assertRaises(OSError):
                storage.save_tasks([make_task("new")], self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob(".cleartask-*")), [])

    def test_export_replace_failure_keeps_destination(self):
        storage.save_tasks([make_task()], self.path)
        target = self.root / "export.csv"
        target.write_bytes(b"previous export")
        with patch("storage.os.replace", side_effect=OSError("simulated export failure")):
            with self.assertRaises(OSError):
                storage.export_tasks(target, self.path)
        self.assertEqual(target.read_bytes(), b"previous export")
        self.assertEqual(list(self.root.glob(".cleartask-*")), [])

    def test_default_path_independent_of_working_directory(self):
        expected = Path(storage.__file__).resolve().parent / "data" / "tasks.csv"
        original = Path.cwd()
        try:
            os.chdir(self.root)
            self.assertEqual(storage.DEFAULT_TASKS_PATH, expected)
            # Intercept IO: this test never touches the actual default CSV.
            with patch("storage._atomic_write") as write:
                storage.save_tasks([])
                write.assert_called_once_with([], expected)
        finally:
            os.chdir(original)

    def test_validation_errors_include_task_context(self):
        with self.assertRaisesRegex(ValueError, r"task\[0\].*task-1.*priority"):
            storage.validate_tasks([make_task(priority="invalid")])


if __name__ == "__main__":
    unittest.main()
