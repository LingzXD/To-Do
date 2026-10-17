"""Private CSV/reference checks shared by V1 and V2, without import cycles."""

import csv
from pathlib import Path

Record = dict[str, object]
V2_FIELDS = {
    "categories": ("id", "name", "color", "position"),
    "tags": ("id", "name", "color"),
    "task_tags": ("task_id", "tag_id"),
    "subtasks": ("id", "task_id", "title", "completed", "position"),
    "events": (
        "id", "title", "description", "event_type", "all_day", "start_at",
        "end_at", "color", "created_at",
    ),
}


def _read_rows(path: Path, fields: tuple[str, ...]) -> list[dict[str, str]]:
    """Read an existing CSV strictly; never create, repair or hide IO errors."""
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        try:
            if reader.fieldnames != list(fields):
                raise ValueError(f"{path}: expected CSV header {fields!r}, got {reader.fieldnames!r}")
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError(f"{path}: CSV line {reader.line_num}: incorrect column count")
                rows.append(row)
        except csv.Error as exc:
            raise ValueError(f"{path}: CSV line {reader.line_num}: {exc}") from exc
    return rows


def _check_references(
    tasks: list[Record], categories: list[Record], tags: list[Record],
    task_tags: list[Record], subtasks: list[Record],
) -> None:
    """Reject dangling references in a candidate store before any write."""
    task_ids = {row["id"] for row in tasks}
    category_ids = {row["id"] for row in categories}
    tag_ids = {row["id"] for row in tags}
    for task in tasks:
        category_id = task["category_id"]
        if category_id is not None and category_id not in category_ids:
            raise ValueError(
                f"tasks.csv: task {task['id']!r} references category {category_id!r} "
                "which does not exist; cannot remove a referenced category"
            )
    for subtask in subtasks:
        if subtask["task_id"] not in task_ids:
            raise ValueError(
                f"subtasks.csv: subtask {subtask['id']!r} references task "
                f"{subtask['task_id']!r} which does not exist; cannot remove a referenced task"
            )
    for link in task_tags:
        for field, ids in (("task_id", task_ids), ("tag_id", tag_ids)):
            if link[field] not in ids:
                raise ValueError(
                    f"task_tags.csv: relation ({link['task_id']!r}, {link['tag_id']!r}): "
                    f"{field} {link[field]!r} does not exist; cannot remove a referenced entity"
                )


def _protect_task_save(tasks: list[Record], path: Path) -> None:
    """Guard V1 writes only when this directory contains V2 reference files.

    This small guard checks CSV shape, IDs and relationships, not every V2 value.
    Full entity validation belongs to storage_v2. Missing V2 tables are empty for
    this check; required references still fail. No files are created.
    """
    directory = path.parent
    names = ("categories", "tags", "task_tags", "subtasks")
    if not any((directory / f"{name}.csv").exists() for name in names):
        return
    if path.name != "tasks.csv":
        raise ValueError("V2 task writes must target tasks.csv in the shared data directory")
    tables: dict[str, list[Record]] = {}
    for name in names:
        sibling = directory / f"{name}.csv"
        rows = _read_rows(sibling, V2_FIELDS[name]) if sibling.exists() else []
        seen = set()
        keys = ("task_id", "tag_id") if name == "task_tags" else ("id",)
        for index, row in enumerate(rows):
            key = tuple(row[field] for field in keys)
            if any(not value.strip() for value in key) or key in seen:
                raise ValueError(f"{sibling}: record[{index}]: blank or duplicate identifier {key!r}")
            if name == "subtasks" and not row["task_id"].strip():
                raise ValueError(f"{sibling}: subtask {row['id']!r}: blank task_id")
            seen.add(key)
        tables[name] = rows
    _check_references(tasks, tables["categories"], tables["tags"], tables["task_tags"], tables["subtasks"])
