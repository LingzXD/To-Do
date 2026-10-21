"""Flask integration for the task service and CSV storage layers."""

from __future__ import annotations

import io
import os
import secrets
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, url_for
from jinja2 import TemplateNotFound
from werkzeug.exceptions import RequestEntityTooLarge

import storage
import task_service


PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
MAX_IMPORT_BYTES = 1024 * 1024


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32),
        TASKS_FILE=storage.DEFAULT_TASKS_PATH,
        MAX_CONTENT_LENGTH=MAX_IMPORT_BYTES,
    )
    if test_config:
        app.config.update(test_config)

    def tasks_path() -> Path:
        return Path(app.config["TASKS_FILE"])

    def page(error: str | None = None, status_code: int = 200):
        try:
            all_tasks = storage.load_tasks(tasks_path())
            stats = task_service.get_statistics(all_tasks)
        except (OSError, ValueError, TypeError, UnicodeError) as exc:
            all_tasks = []
            stats = {"total": 0, "completed": 0, "rate": 0, "avg_progress": 0}
            error = f"无法读取任务数据：{exc}"
            status_code = 500

        stats = {**stats, "pending": stats["total"] - stats["completed"]}
        status_filter = request.args.get("status", "all")
        if status_filter not in {"all", "active", "completed"}:
            status_filter = "all"
        sort_by = request.args.get("sort", "priority")
        if sort_by not in {"priority", "due", "created"}:
            sort_by = "priority"

        visible_tasks = [
            task for task in all_tasks
            if status_filter == "all"
            or (status_filter == "completed") == task["completed"]
        ]
        if sort_by == "due":
            visible_tasks.sort(key=lambda task: (
                task["due_at"] is None,
                task["due_at"] or "",
                PRIORITY_ORDER[task["priority"]],
            ))
        elif sort_by == "created":
            visible_tasks.sort(key=lambda task: task["created_at"], reverse=True)
        else:
            visible_tasks.sort(key=lambda task: (
                PRIORITY_ORDER[task["priority"]],
                task["due_at"] is None,
                task["due_at"] or "",
            ))

        try:
            app.jinja_env.get_template("index.html")
            template = "index.html"
        except TemplateNotFound:
            template = "fallback.html"
        return render_template(
            template,
            tasks=visible_tasks,
            stats=stats,
            error=error,
            status_filter=status_filter,
            sort_by=sort_by,
        ), status_code

    def find_task(tasks: list[dict], task_id: str) -> dict:
        task = next((task for task in tasks if task["id"] == task_id), None)
        if task is None:
            abort(404, description="任务不存在")
        return task

    @app.get("/")
    def index():
        return page()

    @app.post("/add")
    def add():
        title = request.form.get("title", "").strip()
        priority = request.form.get("priority", "medium")
        due_at = request.form.get("due_at", "").strip() or None
        if not title:
            return page("请输入任务标题。", 400)
        if priority not in storage.ALLOWED_PRIORITIES:
            return page("优先级必须是 high、medium 或 low。", 400)
        try:
            tasks = storage.load_tasks(tasks_path())
            task_service.add_task(
                tasks,
                title=title,
                description=request.form.get("description", ""),
                priority=priority,
                due_at=due_at,
            )
            storage.save_tasks(tasks, tasks_path())
        except (OSError, ValueError, TypeError, UnicodeError) as exc:
            return page(f"添加失败：{exc}", 400)
        flash("任务已添加。")
        return redirect(url_for("index"))

    @app.post("/complete/<task_id>")
    def complete(task_id: str):
        try:
            tasks = storage.load_tasks(tasks_path())
            find_task(tasks, task_id)
            task_service.complete_task(tasks, task_id)
            storage.save_tasks(tasks, tasks_path())
        except (OSError, ValueError, TypeError, UnicodeError) as exc:
            return page(f"标记完成失败：{exc}", 409)
        flash("任务已完成。")
        return redirect(url_for("index"))

    @app.post("/delete/<task_id>")
    def delete(task_id: str):
        try:
            tasks = storage.load_tasks(tasks_path())
            find_task(tasks, task_id)
            task_service.delete_task(tasks, task_id)
            storage.save_tasks(tasks, tasks_path())
        except (OSError, ValueError, TypeError, UnicodeError) as exc:
            return page(f"删除失败：{exc}", 409)
        flash("任务已删除。")
        return redirect(url_for("index"))

    @app.post("/priority/<task_id>")
    def priority(task_id: str):
        new_priority = request.form.get("priority", "")
        if new_priority not in storage.ALLOWED_PRIORITIES:
            return page("优先级必须是 high、medium 或 low。", 400)
        try:
            tasks = storage.load_tasks(tasks_path())
            find_task(tasks, task_id)
            task_service.update_task(tasks, task_id, priority=new_priority)
            storage.save_tasks(tasks, tasks_path())
        except (OSError, ValueError, TypeError, UnicodeError) as exc:
            return page(f"修改优先级失败：{exc}", 409)
        flash("优先级已更新。")
        return redirect(url_for("index"))

    @app.get("/export")
    def export():
        try:
            storage.load_tasks(tasks_path())
            output = io.StringIO(newline="")
            storage.export_tasks(output, tasks_path())
        except (OSError, ValueError, TypeError, UnicodeError) as exc:
            return page(f"导出失败：{exc}", 500)
        return app.response_class(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": 'attachment; filename="tasks.csv"'},
        )

    @app.post("/import")
    def import_csv():
        uploaded = request.files.get("file")
        mode = request.form.get("mode", "merge")
        if uploaded is None or not uploaded.filename:
            return page("请选择 CSV 文件。", 400)
        if mode not in {"merge", "replace"}:
            return page("导入方式必须是 merge 或 replace。", 400)
        try:
            source = io.StringIO(uploaded.read().decode("utf-8-sig"), newline="")
            count = storage.import_tasks(source, tasks_path(), mode=mode)
        except (OSError, ValueError, TypeError, UnicodeError) as exc:
            return page(f"导入失败：{exc}", 400)
        flash(f"已导入 {count} 条任务。")
        return redirect(url_for("index"))

    @app.errorhandler(RequestEntityTooLarge)
    def import_too_large(_error):
        return page("上传文件不能超过 1 MB。", 413)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", debug=True)
