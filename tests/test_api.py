"""End-to-end checks for Flask routes and the real CSV persistence layer."""

import io
import tempfile
import unittest
from pathlib import Path

from app import create_app
import storage


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "tasks.csv"
        self.app = create_app({"TESTING": True, "TASKS_FILE": self.path, "SECRET_KEY": "test"})
        self.client = self.app.test_client()

    def add_task(self, title="写报告", priority="medium", **extra):
        return self.client.post(
            "/add", data={"title": title, "priority": priority, **extra},
            follow_redirects=True,
        )

    def test_empty_home_and_export_initialize_storage(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("这里还没有任务".encode(), response.data)
        self.assertEqual(storage.load_tasks(self.path), [])
        exported = self.client.get("/export")
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported.mimetype, "text/csv")
        self.assertEqual(exported.data.decode().strip(), ",".join(storage.CSV_FIELDS))

    def test_add_complete_priority_delete_and_reload(self):
        response = self.add_task(priority="low", due_at="2026-11-01")
        self.assertEqual(response.status_code, 200)
        task = storage.load_tasks(self.path)[0]
        self.assertEqual(task["title"], "写报告")
        self.assertEqual(task["priority"], "low")
        self.assertEqual(task["due_at"], "2026-11-01")
        self.assertEqual(len(task), 12)

        task_id = task["id"]
        self.assertEqual(self.client.post(f"/priority/{task_id}", data={"priority": "high"}).status_code, 302)
        self.assertEqual(storage.load_tasks(self.path)[0]["priority"], "high")
        self.assertEqual(self.client.post(f"/complete/{task_id}").status_code, 302)
        completed = storage.load_tasks(self.path)[0]
        self.assertTrue(completed["completed"])
        self.assertIsNotNone(completed["completed_at"])

        # A fresh app instance sees the saved CSV, not an in-memory list.
        fresh_client = create_app({"TESTING": True, "TASKS_FILE": self.path}).test_client()
        self.assertIn("已完成 1 项".encode(), fresh_client.get("/").data)
        self.assertEqual(fresh_client.post(f"/delete/{task_id}").status_code, 302)
        self.assertEqual(storage.load_tasks(self.path), [])

    def test_invalid_form_does_not_change_csv(self):
        self.assertEqual(self.add_task().status_code, 200)
        before = self.path.read_bytes()
        self.assertEqual(self.add_task(title=" ").status_code, 400)
        self.assertEqual(self.add_task(title="invalid", priority="urgent").status_code, 400)
        self.assertEqual(self.add_task(title="invalid", due_at="not-a-date").status_code, 400)
        task_id = storage.load_tasks(self.path)[0]["id"]
        self.assertEqual(self.client.post(f"/priority/{task_id}", data={"priority": "urgent"}).status_code, 400)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.client.post("/complete/unknown").status_code, 404)

    def test_import_export_and_failed_merge_preserves_existing_data(self):
        self.add_task()
        exported = self.client.get("/export").data
        original = storage.load_tasks(self.path)
        failed = self.client.post(
            "/import", data={"file": (io.BytesIO(exported), "tasks.csv"), "mode": "merge"},
        )
        self.assertEqual(failed.status_code, 400)
        self.assertEqual(storage.load_tasks(self.path), original)

        self.client.post(f"/delete/{original[0]['id']}")
        restored = self.client.post(
            "/import", data={"file": (io.BytesIO(exported), "tasks.csv"), "mode": "merge"},
        )
        self.assertEqual(restored.status_code, 302)
        self.assertEqual(storage.load_tasks(self.path), original)

        replaced = self.client.post(
            "/import", data={"file": (io.BytesIO(exported), "tasks.csv"), "mode": "replace"},
        )
        self.assertEqual(replaced.status_code, 302)
        self.assertEqual(storage.load_tasks(self.path), original)

    def test_bad_csv_and_upload_limit(self):
        bad = self.client.post(
            "/import", data={"file": (io.BytesIO(b"broken"), "bad.csv"), "mode": "replace"},
        )
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(storage.load_tasks(self.path), [])
        oversized = self.client.post(
            "/import", data={"file": (io.BytesIO(b"x" * (1024 * 1024 + 1)), "big.csv")},
        )
        self.assertEqual(oversized.status_code, 413)


if __name__ == "__main__":
    unittest.main()
