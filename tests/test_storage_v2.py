"""Isolated V2 contract, integration and fault-injection tests (stdlib only)."""

import copy
import csv
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import storage
import storage_v2 as v2
from test_storage import make_task


def category(entity_id="cat-1", **changes):
    row = {"id": entity_id, "name": "学习", "color": None, "position": 0}
    row.update(changes)
    return row


def tag(entity_id="tag-1", **changes):
    row = {"id": entity_id, "name": "重要", "color": "#aB12F0"}
    row.update(changes)
    return row


def subtask(entity_id="sub-1", **changes):
    row = {"id": entity_id, "task_id": "task-1", "title": "子任务", "completed": False, "position": 0}
    row.update(changes)
    return row


def event(entity_id="event-1", **changes):
    row = {
        "id": entity_id, "title": "课程", "description": "", "event_type": "schedule",
        "all_day": False, "start_at": "2026-10-17T10:00:00",
        "end_at": "2026-10-17T11:00:00", "color": None, "created_at": "2026-10-17T09:00:00",
    }
    row.update(changes)
    return row


def link(task_id="task-1", tag_id="tag-1"):
    return {"task_id": task_id, "tag_id": tag_id}


class V2Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / "data"
        v2.initialize_v2_storage(self.data)

    def path(self, entity):
        return self.data / f"{entity}.csv"

    def save_tasks(self, tasks):
        storage.save_tasks(tasks, self.path("tasks"))

    def seed(self):
        v2.save_categories([category()], self.path("categories"))
        self.save_tasks([make_task(category_id="cat-1"), make_task("task-2")])
        v2.save_tags([tag(), tag("tag-2", name="截止")], self.path("tags"))
        v2.save_subtasks([subtask()], self.path("subtasks"))
        v2.save_task_tags([link()], self.path("task_tags"))

    def snapshot(self):
        return {path.name: path.read_bytes() for path in self.data.glob("*.csv")}

    def write_raw(self, entity, rows, fields=None):
        with self.path(entity).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields or v2.V2_FIELDS[entity])
            writer.writeheader()
            for record in rows:
                writer.writerow({key: "" if value is None else str(value).lower() if type(value) is bool else value for key, value in record.items()})

    def assert_invalid(self, entity, record, exception=ValueError):
        before = self.snapshot()
        with self.assertRaises(exception):
            getattr(v2, f"save_{entity}")([record], self.path(entity))
        self.assertEqual(self.snapshot(), before)

    def test_initialize_all_headers(self):
        for entity, fields in {"tasks": storage.CSV_FIELDS, **v2.V2_FIELDS}.items():
            with self.subTest(entity=entity), self.path(entity).open(encoding="utf-8", newline="") as stream:
                self.assertEqual(list(csv.reader(stream)), [list(fields)])
        v2.validate_store_integrity(self.data)

    def test_initialize_nested_new_directory(self):
        target = self.root / "nested" / "new"
        v2.initialize_v2_storage(target)
        self.assertEqual(len(list(target.glob("*.csv"))), 6)

    def test_reinitialize_preserves_every_file(self):
        self.seed()
        before = self.snapshot()
        v2.initialize_v2_storage(self.data)
        self.assertEqual(self.snapshot(), before)

    def test_initialize_preserves_existing_v1_tasks(self):
        target = self.root / "legacy"
        storage.save_tasks([make_task()], target / "tasks.csv")
        before = (target / "tasks.csv").read_bytes()
        v2.initialize_v2_storage(target)
        self.assertEqual((target / "tasks.csv").read_bytes(), before)
        v2.validate_store_integrity(target)

    def test_initialize_corrupt_existing_data_does_not_create_other_files(self):
        target = self.root / "broken"
        target.mkdir()
        (target / "events.csv").write_bytes(b"wrong\n")
        with self.assertRaises(ValueError):
            v2.initialize_v2_storage(target)
        self.assertEqual(list(target.iterdir()), [target / "events.csv"])
        self.assertEqual((target / "events.csv").read_bytes(), b"wrong\n")

    def test_initialize_legacy_unresolved_category_fails_without_changes(self):
        target = self.root / "legacy-category"
        storage.save_tasks([make_task(category_id="legacy-cat")], target / "tasks.csv")
        before = (target / "tasks.csv").read_bytes()
        with self.assertRaisesRegex(ValueError, "legacy-cat"):
            v2.initialize_v2_storage(target)
        self.assertEqual(list(target.iterdir()), [target / "tasks.csv"])
        self.assertEqual((target / "tasks.csv").read_bytes(), before)

    def test_legacy_category_can_be_explicitly_defined_before_upgrade(self):
        target = self.root / "legacy-defined"
        storage.save_tasks([make_task(category_id="legacy-cat")], target / "tasks.csv")
        before = (target / "tasks.csv").read_bytes()
        v2.save_categories([category("legacy-cat")], target / "categories.csv")
        v2.initialize_v2_storage(target)
        self.assertEqual((target / "tasks.csv").read_bytes(), before)
        v2.validate_store_integrity(target)

    def test_initialize_partial_failure_can_be_retried(self):
        target = self.root / "initialization-failure"
        original_write = v2._write_stream

        def fail_on_tags(rows, stream, entity):
            if entity == "tags":
                stream.write("incomplete")
                raise OSError("simulated initialization failure")
            original_write(rows, stream, entity)

        with patch("storage_v2._write_stream", side_effect=fail_on_tags):
            with self.assertRaises(OSError):
                v2.initialize_v2_storage(target)
        self.assertTrue((target / "tasks.csv").exists())
        self.assertTrue((target / "categories.csv").exists())
        self.assertFalse((target / "tags.csv").exists())
        v2.initialize_v2_storage(target)
        v2.validate_store_integrity(target)

    def test_initialize_v1_header_failure_removes_only_new_file(self):
        target = self.root / "v1-init-failure"
        with patch("storage._write_stream", side_effect=OSError("simulated failure")):
            with self.assertRaises(OSError):
                v2.initialize_v2_storage(target)
        self.assertFalse((target / "tasks.csv").exists())
        v2.initialize_v2_storage(target)
        v2.validate_store_integrity(target)

    def test_category_roundtrip_and_order(self):
        records = [category("c", name="C", position=2), category("b", name="B", position=0), category("a", name="A", color="#123ABC", position=0)]
        original = copy.deepcopy(records)
        v2.save_categories(records, self.path("categories"))
        self.assertEqual(v2.load_categories(self.path("categories")), [records[2], records[1], records[0]])
        self.assertEqual(records, original)

    def test_category_blank_names_and_ids(self):
        for field in ("id", "name"):
            for value in ("", " \t\n"):
                with self.subTest(field=field, value=value):
                    self.assert_invalid("categories", category(**{field: value}))

    def test_category_duplicate_ids(self):
        with self.assertRaisesRegex(ValueError, "duplicate ID"):
            v2.save_categories([category(), category(name="another")], self.path("categories"))

    def test_category_normalized_duplicate_names(self):
        for left, right in ((" Study ", "study"), ("Straße", "STRASSE"), ("学习", " 学习 ")):
            with self.subTest(left=left), self.assertRaisesRegex(ValueError, "normalized name"):
                v2.validate_categories([category("a", name=left), category("b", name=right)])

    def test_invalid_colors_all_colored_entities(self):
        for entity, factory in (("categories", category), ("tags", tag), ("events", event)):
            for value in ("", "red", "#fff", "#GG0000", "#12345678", " #123456"):
                with self.subTest(entity=entity, color=value):
                    self.assert_invalid(entity, factory(color=value))
            self.assert_invalid(entity, factory(color=123), TypeError)

    def test_positions_strict_nonnegative_integer(self):
        self.save_tasks([make_task()])
        for entity, factory in (("categories", category), ("subtasks", subtask)):
            for value, error in ((True, TypeError), (False, TypeError), (1.0, TypeError), ("1", TypeError), (-1, ValueError)):
                with self.subTest(entity=entity, value=value):
                    self.assert_invalid(entity, factory(position=value), error)

    def test_task_cannot_reference_missing_category(self):
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "task-1.*missing"):
            self.save_tasks([make_task(category_id="missing")])
        self.assertEqual(self.snapshot(), before)

    def test_task_valid_category_and_query(self):
        v2.save_categories([category()], self.path("categories"))
        self.save_tasks([make_task(category_id="cat-1"), make_task("task-2")])
        self.assertEqual(v2.get_tasks_for_category("cat-1", self.data), [make_task(category_id="cat-1")])

    def test_referenced_category_cannot_be_removed(self):
        self.seed()
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "task-1.*cat-1"):
            v2.save_categories([], self.path("categories"))
        self.assertEqual(self.snapshot(), before)

    def test_tag_roundtrip_chinese_and_normalized_uniqueness(self):
        v2.save_tags([tag()], self.path("tags"))
        self.assertEqual(v2.load_tags(self.path("tags")), [tag()])
        for records in ([tag(), tag(name="another")], [tag("a", name="  Straße"), tag("b", name="STRASSE ")]):
            with self.assertRaises(ValueError):
                v2.validate_tags(records)

    def test_tag_blank_name(self):
        self.assert_invalid("tags", tag(name=" \n"))

    def test_many_to_many_relations_and_tag_query(self):
        self.seed()
        records = [link("task-1", "tag-2"), link("task-2", "tag-1"), link()]
        v2.save_task_tags(records, self.path("task_tags"))
        self.assertEqual(v2.load_task_tags(self.path("task_tags")), records)
        self.assertEqual(v2.get_tag_ids_for_task("task-1", self.data), ["tag-1", "tag-2"])
        self.assertEqual(v2.get_tag_ids_for_task("task-2", self.data), ["tag-1"])

    def test_duplicate_relation_rejected(self):
        self.seed()
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "duplicate relation"):
            v2.save_task_tags([link(), link()], self.path("task_tags"))
        self.assertEqual(self.snapshot(), before)

    def test_missing_task_and_tag_references(self):
        self.seed()
        self.assert_invalid("task_tags", link("missing", "tag-1"))
        self.assert_invalid("task_tags", link("task-1", "missing"))
        self.assert_invalid("subtasks", subtask(task_id="missing"))

    def test_blank_relation_fields(self):
        for field in ("task_id", "tag_id"):
            row = link()
            row[field] = " "
            self.assert_invalid("task_tags", row)

    def test_referenced_tag_cannot_be_removed(self):
        self.seed()
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "task-1.*tag-1"):
            v2.save_tags([], self.path("tags"))
        self.assertEqual(self.snapshot(), before)

    def test_subtask_roundtrip_and_stable_query_order(self):
        self.save_tasks([make_task(), make_task("task-2")])
        rows = [subtask("b", completed=True, position=2), subtask("a", position=2), subtask("c", position=0), subtask("d", task_id="task-2")]
        v2.save_subtasks(rows, self.path("subtasks"))
        loaded = v2.load_subtasks(self.path("subtasks"))
        self.assertEqual(loaded, rows)
        self.assertIs(loaded[0]["completed"], True)
        self.assertEqual(v2.get_subtasks_for_task("task-1", self.data), [rows[2], rows[1], rows[0]])

    def test_subtasks_one_level_and_duplicate_ids(self):
        self.save_tasks([make_task()])
        self.assert_invalid("subtasks", subtask(parent_subtask_id="sub-2"))
        with self.assertRaisesRegex(ValueError, "duplicate ID"):
            v2.validate_subtasks([subtask(), subtask(task_id="task-2")])
        self.assert_invalid("subtasks", subtask(title="  "))

    def test_subtask_completed_requires_bool(self):
        for value in ("false", "true", 0, 1, None):
            with self.subTest(value=value):
                self.assert_invalid("subtasks", subtask(completed=value), TypeError)

    def test_v1_save_rejects_subtask_orphan(self):
        self.save_tasks([make_task()])
        v2.save_subtasks([subtask()], self.path("subtasks"))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "sub-1.*task-1"):
            self.save_tasks([])
        self.assertEqual(self.snapshot(), before)

    def test_v1_save_rejects_task_tag_orphan(self):
        self.save_tasks([make_task()])
        v2.save_tags([tag()], self.path("tags"))
        v2.save_task_tags([link()], self.path("task_tags"))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "task-1.*tag-1"):
            self.save_tasks([])
        self.assertEqual(self.snapshot(), before)

    def test_v1_replace_import_cannot_bypass_subtask_guard(self):
        self.save_tasks([make_task()])
        v2.save_subtasks([subtask()], self.path("subtasks"))
        before = self.snapshot()
        source = io.StringIO()
        csv.writer(source).writerow(storage.CSV_FIELDS)
        source.seek(0)
        with self.assertRaisesRegex(ValueError, "sub-1"):
            storage.import_tasks(source, self.path("tasks"), mode="replace")
        self.assertEqual(self.snapshot(), before)

    def test_v1_replace_import_cannot_bypass_tag_guard(self):
        self.save_tasks([make_task()])
        v2.save_tags([tag()], self.path("tags"))
        v2.save_task_tags([link()], self.path("task_tags"))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "tag-1"):
            storage.import_tasks(io.StringIO(",".join(storage.CSV_FIELDS) + "\n"), self.path("tasks"), mode="replace")
        self.assertEqual(self.snapshot(), before)

    def test_v1_replace_import_retaining_references_succeeds(self):
        self.seed()
        source = io.StringIO(newline="")
        storage.export_tasks(source, self.path("tasks"))
        source.seek(0)
        self.assertEqual(storage.import_tasks(source, self.path("tasks"), mode="replace"), 2)
        v2.validate_store_integrity(self.data)

    def test_remove_relations_before_deleting_task_or_tag(self):
        self.seed()
        v2.save_subtasks([], self.path("subtasks"))
        v2.save_task_tags([], self.path("task_tags"))
        v2.save_tags([], self.path("tags"))
        self.save_tasks([])
        v2.save_categories([], self.path("categories"))
        v2.validate_store_integrity(self.data)

    def test_pure_v1_compatibility_with_unresolved_category(self):
        path = self.root / "v1" / "custom.csv"
        task = make_task(category_id="legacy")
        storage.save_tasks([task], path)
        self.assertEqual(storage.load_tasks(path), [task])
        storage.save_tasks([], path)
        self.assertEqual(storage.load_tasks(path), [])

    def test_integrity_finds_each_dangling_reference(self):
        self.seed()
        originals = self.snapshot()
        cases = [
            ("subtasks", [subtask(task_id="ghost")], "sub-1.*ghost"),
            ("task_tags", [link("ghost", "tag-1")], "ghost"),
            ("task_tags", [link("task-1", "ghost")], "ghost"),
        ]
        for entity, rows, message in cases:
            with self.subTest(entity=entity, rows=rows):
                self.write_raw(entity, rows)
                with self.assertRaisesRegex(ValueError, message):
                    v2.validate_store_integrity(self.data)
                self.path(entity).write_bytes(originals[f"{entity}.csv"])
        # Bypass validation deliberately to simulate damaged category references.
        self.path("tasks").write_text(self.path("tasks").read_text(encoding="utf-8").replace("cat-1", "ghost"), encoding="utf-8", newline="")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "task-1.*ghost"):
            v2.validate_store_integrity(self.data)
        self.assertEqual(self.snapshot(), before)

    def test_integrity_distinguishes_missing_corrupt_and_empty(self):
        self.path("events").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "not initialized"):
            v2.validate_store_integrity(self.data)
        self.assertFalse(self.path("events").exists())
        self.path("events").write_bytes(b"")
        with self.assertRaises(ValueError):
            v2.validate_store_integrity(self.data)
        self.assertEqual(self.path("events").read_bytes(), b"")

    def test_all_saves_check_candidate_store_integrity(self):
        self.write_raw("subtasks", [subtask(task_id="ghost")])
        before = self.snapshot()
        for entity, records in (("categories", []), ("tags", []), ("task_tags", []), ("events", [])):
            with self.subTest(entity=entity), self.assertRaisesRegex(ValueError, "ghost"):
                getattr(v2, f"save_{entity}")(records, self.path(entity))
            self.assertEqual(self.snapshot(), before)

    def test_query_nonexistent_entities_and_empty_results(self):
        self.save_tasks([make_task()])
        v2.save_categories([category()], self.path("categories"))
        self.assertEqual(v2.get_subtasks_for_task("task-1", self.data), [])
        self.assertEqual(v2.get_tag_ids_for_task("task-1", self.data), [])
        self.assertEqual(v2.get_tasks_for_category("cat-1", self.data), [])
        for query in (v2.get_subtasks_for_task, v2.get_tag_ids_for_task, v2.get_tasks_for_category):
            with self.subTest(query=query.__name__), self.assertRaises(ValueError):
                query("ghost", self.data)
            with self.assertRaises(TypeError):
                query(123, self.data)

    def test_manual_progress_values(self):
        for value in (0, 50, 100):
            with self.subTest(value=value):
                self.assertEqual(v2.calculate_task_progress(make_task(manual_progress=value), []), value)

    def test_subtask_progress_ratios(self):
        task = make_task(progress_mode="subtasks")
        for total, done, expected in ((0, 0, 0), (3, 0, 0), (3, 1, 33), (3, 2, 67), (3, 3, 100), (4, 3, 75), (8, 1, 13)):
            rows = [subtask(f"s-{index}", completed=index < done) for index in range(total)]
            with self.subTest(total=total, done=done):
                self.assertEqual(v2.calculate_task_progress(task, rows), expected)

    def test_completed_task_reports_100_without_mutation(self):
        task = make_task(completed=True, progress_mode="subtasks", manual_progress=17)
        rows = [subtask()]
        before = copy.deepcopy((task, rows))
        self.assertEqual(v2.calculate_task_progress(task, rows), 100)
        self.assertEqual((task, rows), before)

    def test_progress_ignores_other_tasks_without_mutation(self):
        task = make_task(progress_mode="subtasks", manual_progress=17)
        rows = [subtask("s-1", completed=True), subtask("s-2"), subtask("s-3", task_id="other", completed=True)]
        before = copy.deepcopy((task, rows))
        self.assertEqual(v2.calculate_task_progress(task, rows), 50)
        self.assertEqual((task, rows), before)

    def test_progress_rejects_invalid_data_even_for_completed_or_manual(self):
        with self.assertRaises(ValueError):
            v2.calculate_task_progress(make_task(progress_mode="bad"), [])
        for task in (make_task(), make_task(completed=True), make_task(progress_mode="subtasks")):
            with self.subTest(task=task), self.assertRaises(TypeError):
                v2.calculate_task_progress(task, [subtask(completed="false")])
        with self.assertRaises(ValueError):
            v2.calculate_task_progress(make_task(), [subtask(), subtask()])

    def test_event_schedule_and_special_roundtrip(self):
        rows = [event(), event("special", event_type="special", color="#00ffAA")]
        v2.save_events(rows, self.path("events"))
        self.assertEqual(v2.load_events(self.path("events")), rows)

    def test_event_all_day_single_multi_day_and_leap_day(self):
        rows = [event("one", all_day=True, start_at="2028-02-29", end_at="2028-02-29"), event("many", all_day=True, start_at="2026-10-17", end_at="2026-10-20", event_type="special")]
        v2.save_events(rows, self.path("events"))
        self.assertEqual(v2.load_events(self.path("events")), rows)

    def test_event_timed_cross_day(self):
        row = event(start_at="2026-10-17T23:30:00", end_at="2026-10-18T00:30:00")
        v2.save_events([row], self.path("events"))
        self.assertEqual(v2.load_events(self.path("events")), [row])

    def test_event_reverse_and_equal_boundaries(self):
        self.assert_invalid("events", event(end_at="2026-10-17T09:00:00"))
        self.assert_invalid("events", event(end_at="2026-10-17T10:00:00"))
        self.assert_invalid("events", event(all_day=True, start_at="2026-10-18", end_at="2026-10-17"))

    def test_event_mixed_and_wrong_boundary_formats(self):
        for changes in (
            {"all_day": True}, {"start_at": "2026-10-17"},
            {"end_at": "2026-10-18"}, {"start_at": "2026-10-17", "end_at": "2026-10-18"},
            {"all_day": True, "start_at": "2026-10-17", "end_at": "2026-10-18T10:00:00"},
        ):
            with self.subTest(changes=changes):
                self.assert_invalid("events", event(**changes))

    def test_event_invalid_dates_times_and_types(self):
        for field in ("start_at", "end_at", "created_at"):
            for value in ("2026-02-29T10:00:00", "2026-10-17T24:00:00", "2026-10-17T10:00:00Z", "2026-10-17T10:00:00.5", "2026-10-17 10:00:00", "2026-10-17T10:00:00+08:00"):
                with self.subTest(field=field, value=value):
                    self.assert_invalid("events", event(**{field: value}))
        self.assert_invalid("events", event(created_at="2026-10-17"))
        self.assert_invalid("events", event(event_type="birthday"))
        self.assert_invalid("events", event(all_day=1), TypeError)
        self.assert_invalid("events", event(description=None), TypeError)
        self.assert_invalid("events", event(title=" "))

    def test_entity_python_types_missing_unknown_and_duplicate_ids(self):
        factories = {"categories": category, "tags": tag, "subtasks": subtask, "events": event, "task_tags": link}
        for entity, factory in factories.items():
            validate = getattr(v2, f"validate_{entity}")
            for value in (None, {}, (), [None], [[]]):
                with self.subTest(entity=entity, value=value), self.assertRaises(TypeError):
                    validate(value)
            row = factory()
            row.pop(next(iter(row)))
            with self.assertRaises(ValueError):
                validate([row])
            row = factory()
            row["unexpected"] = "never silently discard"
            with self.assertRaises(ValueError):
                validate([row])
            with self.assertRaises(ValueError):
                validate([factory(), factory()])

    def test_csv_special_characters_across_all_entities(self):
        special = '中文, "引号"\n第二行\r\n第三行'
        v2.save_categories([category(name=special)], self.path("categories"))
        v2.save_tags([tag(name=special)], self.path("tags"))
        self.save_tasks([make_task()])
        v2.save_subtasks([subtask(title=special)], self.path("subtasks"))
        v2.save_events([event(title=special, description=special)], self.path("events"))
        self.assertEqual(v2.load_categories(self.path("categories"))[0]["name"], special)
        self.assertEqual(v2.load_tags(self.path("tags"))[0]["name"], special)
        self.assertEqual(v2.load_subtasks(self.path("subtasks"))[0]["title"], special)
        self.assertEqual(v2.load_events(self.path("events"))[0]["description"], special)
        for path in self.data.glob("*.csv"):
            path.read_bytes().decode("utf-8")
            self.assertFalse(path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_csv_header_and_column_errors_all_entities(self):
        for entity, fields in v2.V2_FIELDS.items():
            original = self.path(entity).read_bytes()
            contents = ["", ",".join(fields[:-1]) + "\n", ",".join((*fields, "extra")) + "\n", ",".join((*fields, fields[0])) + "\n", ",".join(reversed(fields)) + "\n", ",".join(fields) + '\n"unterminated', ",".join(fields) + "\none\n", ",".join(fields) + "\n" + ",".join(["x"] * (len(fields) + 1)) + "\n"]
            for content in contents:
                with self.subTest(entity=entity, content=content):
                    self.path(entity).write_text(content, encoding="utf-8", newline="")
                    before = self.path(entity).read_bytes()
                    with self.assertRaises(ValueError):
                        getattr(v2, f"load_{entity}")(self.path(entity))
                    self.assertEqual(self.path(entity).read_bytes(), before)
            self.path(entity).write_bytes(original)

    def test_csv_duplicate_records_all_entities(self):
        for entity, row in (("categories", category()), ("tags", tag()), ("subtasks", subtask()), ("events", event()), ("task_tags", link())):
            self.write_raw(entity, [row, row])
            with self.subTest(entity=entity), self.assertRaisesRegex(ValueError, "duplicate"):
                getattr(v2, f"load_{entity}")(self.path(entity))

    def test_csv_boolean_conversion_and_rejection(self):
        for entity, row, field in (("subtasks", subtask(), "completed"), ("events", event(), "all_day")):
            row[field] = "FALSE"
            self.write_raw(entity, [row])
            self.assertIs(getattr(v2, f"load_{entity}")(self.path(entity))[0][field], False)
            row[field] = "yes"
            self.write_raw(entity, [row])
            with self.assertRaises(ValueError):
                getattr(v2, f"load_{entity}")(self.path(entity))

    def test_csv_invalid_position(self):
        for value in ("1.5", "True", "", "-1"):
            self.write_raw("categories", [category(position=value)])
            with self.subTest(value=value), self.assertRaises(ValueError):
                v2.load_categories(self.path("categories"))

    def test_bom_is_accepted_and_encoding_errors_propagate(self):
        v2.save_tags([tag()], self.path("tags"))
        self.path("tags").write_bytes(b"\xef\xbb\xbf" + self.path("tags").read_bytes())
        self.assertEqual(v2.load_tags(self.path("tags")), [tag()])
        self.path("tags").write_bytes(b"\xff\xfe")
        with self.assertRaises(UnicodeDecodeError):
            v2.load_tags(self.path("tags"))

    def test_all_atomic_save_replace_failures_preserve_store(self):
        self.seed()
        for entity, rows in (("categories", [category()]), ("tags", [tag(), tag("tag-2", name="截止")]), ("subtasks", [subtask()]), ("task_tags", [link()]), ("events", [event()])):
            before = self.snapshot()
            with self.subTest(entity=entity), patch("storage_v2.os.replace", side_effect=OSError("simulated failure")):
                with self.assertRaises(OSError):
                    getattr(v2, f"save_{entity}")(rows, self.path(entity))
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(list(self.data.glob(".cleartask-*")), [])

    def test_partial_write_failure_preserves_old_file_and_cleans_temp(self):
        v2.save_events([event()], self.path("events"))
        before = self.snapshot()

        def fail_after_header(rows, stream, entity):
            stream.write("partial data")
            raise OSError("simulated disk full")

        with patch("storage_v2._write_stream", side_effect=fail_after_header):
            with self.assertRaises(OSError):
                v2.save_events([], self.path("events"))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.data.glob(".cleartask-*")), [])

    def test_fsync_failure_preserves_old_file_and_cleans_temp(self):
        before = self.snapshot()
        with patch("storage_v2.os.fsync", side_effect=OSError("simulated fsync failure")):
            with self.assertRaises(OSError):
                v2.save_events([event()], self.path("events"))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.data.glob(".cleartask-*")), [])

    def test_v1_guard_rejects_corrupt_reference_csv_before_save(self):
        self.seed()
        self.path("subtasks").write_bytes(b"wrong,header\n")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "subtasks.csv"):
            self.save_tasks([])
        self.assertEqual(self.snapshot(), before)

    def test_noncanonical_filename_is_rejected_in_v2_context(self):
        with self.assertRaises(ValueError):
            v2.save_tags([], self.data / "other.csv")
        with self.assertRaises(ValueError):
            storage.save_tasks([], self.data / "other.csv")
        self.assertFalse((self.data / "other.csv").exists())

    def test_load_missing_never_creates_file(self):
        self.path("tags").unlink()
        with self.assertRaises(FileNotFoundError):
            v2.load_tags(self.path("tags"))
        self.assertFalse(self.path("tags").exists())

    def test_explicit_directory_queries_never_use_default_data(self):
        self.seed()
        with patch.object(v2, "DEFAULT_DATA_DIR", self.root / "never-use"):
            self.assertEqual(v2.get_tag_ids_for_task("task-1", self.data), ["tag-1"])
            self.assertEqual(len(v2.get_subtasks_for_task("task-1", self.data)), 1)
            v2.validate_store_integrity(self.data)

    def test_default_paths_stable_and_no_real_data_access(self):
        expected = Path(v2.__file__).resolve().parent / "data"
        original = Path.cwd()
        try:
            os.chdir(self.root)
            self.assertEqual(v2.DEFAULT_DATA_DIR, expected)
            for entity in v2.V2_FIELDS:
                with patch("storage_v2._save_entity") as save:
                    getattr(v2, f"save_{entity}")([])
                    save.assert_called_once_with([], expected / f"{entity}.csv", entity)
        finally:
            os.chdir(original)


if __name__ == "__main__":
    unittest.main()
