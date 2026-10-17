"""ClearTask V2 entity persistence and queries; single process, single writer.

Type errors raise TypeError, invalid data/CSV/references raise ValueError,
filesystem errors raise OSError, invalid UTF-8 raises UnicodeError. All saves
validate the candidate store before replacing ONE file. No cross-file transaction.
"""

import csv
import os
import re
import tempfile
from pathlib import Path
from typing import TextIO

import storage
from _storage_refs import Record, V2_FIELDS, _check_references, _read_rows

PathLike = storage.PathLike
DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_CATEGORIES_PATH = DEFAULT_DATA_DIR / "categories.csv"
DEFAULT_TAGS_PATH = DEFAULT_DATA_DIR / "tags.csv"
DEFAULT_TASK_TAGS_PATH = DEFAULT_DATA_DIR / "task_tags.csv"
DEFAULT_SUBTASKS_PATH = DEFAULT_DATA_DIR / "subtasks.csv"
DEFAULT_EVENTS_PATH = DEFAULT_DATA_DIR / "events.csv"
CATEGORY_FIELDS = V2_FIELDS["categories"]
TAG_FIELDS = V2_FIELDS["tags"]
TASK_TAG_FIELDS = V2_FIELDS["task_tags"]
SUBTASK_FIELDS = V2_FIELDS["subtasks"]
EVENT_FIELDS = V2_FIELDS["events"]
ALLOWED_EVENT_TYPES = frozenset({"schedule", "special"})


def _text(value: object, field: str, *, blank: bool = False) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be str")
    if not blank and not value.strip():
        raise ValueError(f"{field} must not be blank")


def _validate_record(record: Record, entity: str) -> None:
    fields = V2_FIELDS[entity]
    if not isinstance(record, dict):
        raise TypeError("record must be dict")
    if set(record) != set(fields):
        raise ValueError(f"expected fields {fields!r}; got {list(record)!r}")
    for field in fields:
        value = record[field]
        if field == "color":
            if value is not None:
                _text(value, field)
                if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
                    raise ValueError("color must be None or #RRGGBB")
        elif field == "position":
            if type(value) is not int:
                raise TypeError("position must be int, not bool")
            if value < 0:
                raise ValueError("position must be nonnegative")
        elif field in {"completed", "all_day"}:
            if type(value) is not bool:
                raise TypeError(f"{field} must be bool")
        else:
            _text(value, field, blank=field == "description")
    if entity == "events":
        if record["event_type"] not in ALLOWED_EVENT_TYPES:
            raise ValueError("event_type must be schedule or special")
        for field in ("start_at", "end_at", "created_at"):
            value = record[field]
            all_day = record["all_day"] and field != "created_at"
            try:
                storage._validate_time(value, date_allowed=bool(all_day))
                if all_day and len(value) != 10:
                    raise ValueError("all-day boundaries must be YYYY-MM-DD")
            except (TypeError, ValueError) as exc:
                raise type(exc)(f"{field}: {exc}") from exc
        start, end = record["start_at"], record["end_at"]
        if end < start or (not record["all_day"] and end == start):
            raise ValueError("end_at must be >= start_at for all-day, > start_at otherwise")


def _validate_records(records: list[Record], entity: str) -> None:
    if not isinstance(records, list):
        raise TypeError(f"{entity} must be a list")
    seen_ids: set[object] = set()
    seen_names: set[str] = set()
    for index, record in enumerate(records):
        try:
            _validate_record(record, entity)
            key = (record["task_id"], record["tag_id"]) if entity == "task_tags" else record["id"]
            if key in seen_ids:
                raise ValueError(f"duplicate {'relation' if entity == 'task_tags' else 'ID'} {key!r}")
            seen_ids.add(key)
            if entity in {"categories", "tags"}:
                name = record["name"].strip().casefold()
                if name in seen_names:
                    raise ValueError(f"duplicate normalized name {record['name']!r}")
                seen_names.add(name)
        except (TypeError, ValueError) as exc:
            identity = record.get("id", record) if isinstance(record, dict) else record
            raise type(exc)(f"{entity}.csv record[{index}] {identity!r}: {exc}") from exc


def validate_categories(categories: list[Record]) -> None:
    """Validate full Category schema, unique IDs/names; no IO or mutation. Return None."""
    _validate_records(categories, "categories")


def validate_tags(tags: list[Record]) -> None:
    """Validate full Tag schema and unique IDs/normalized names; return None, no IO."""
    _validate_records(tags, "tags")


def validate_task_tags(task_tags: list[Record]) -> None:
    """Validate relation fields/unique pairs; no IO. Foreign keys checked on save."""
    _validate_records(task_tags, "task_tags")


def validate_subtasks(subtasks: list[Record]) -> None:
    """Validate one-level Subtask schema/unique IDs; foreign keys checked on save."""
    _validate_records(subtasks, "subtasks")


def validate_events(events: list[Record]) -> None:
    """Validate Event IDs, types and exact local time boundaries; no IO, return None."""
    _validate_records(events, "events")


def _entity_path(filepath: PathLike, entity: str) -> Path:
    path = Path(filepath)
    if path.name != f"{entity}.csv":
        raise ValueError(f"{entity} filepath must end in {entity}.csv; choose its parent data directory")
    if path.is_symlink():
        raise ValueError(f"{path}: symlinked entity files are not supported")
    return path


def _load_entity(path: Path, entity: str) -> list[Record]:
    rows = _read_rows(path, V2_FIELDS[entity])
    records: list[Record] = []
    for index, row in enumerate(rows):
        record: Record = dict(row)
        try:
            for field in V2_FIELDS[entity]:
                value = row[field]
                if field == "color":
                    record[field] = value or None
                elif field == "position":
                    if not re.fullmatch(r"[0-9]+", value):
                        raise ValueError("position must contain decimal integer digits")
                    record[field] = int(value)
                elif field in {"completed", "all_day"}:
                    if value.lower() not in {"true", "false"}:
                        raise ValueError(f"{field} must be true or false")
                    record[field] = value.lower() == "true"
            records.append(record)
        except (TypeError, ValueError) as exc:
            raise type(exc)(f"{path}: record[{index}]: {exc}") from exc
    try:
        _validate_records(records, entity)
    except (TypeError, ValueError) as exc:
        raise type(exc)(f"{path}: {exc}") from exc
    return records


def _write_stream(records: list[Record], stream: TextIO, entity: str) -> None:
    writer = csv.DictWriter(stream, fieldnames=V2_FIELDS[entity])
    writer.writeheader()
    for record in records:
        row = {
            field: "" if value is None else str(value).lower() if type(value) is bool else str(value)
            for field, value in record.items()
        }
        writer.writerow(row)


def _atomic_write(records: list[Record], path: Path, entity: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent,
            prefix=".cleartask-", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            _write_stream(records, stream, entity)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_store(
    directory: Path, *, require_all: bool = False,
    candidate_entity: str | None = None, candidate: list[Record] | None = None,
) -> dict[str, list[Record]]:
    """Read siblings without auto-initialization, optionally substituting one candidate."""
    tables: dict[str, list[Record]] = {}
    for entity in ("tasks", *V2_FIELDS):
        path = directory / f"{entity}.csv"
        if entity == candidate_entity:
            tables[entity] = candidate
        elif path.exists():
            if path.is_symlink():
                raise ValueError(f"{path}: symlinked entity files are not supported")
            tables[entity] = storage._read_path(path) if entity == "tasks" else _load_entity(path, entity)
        elif require_all:
            raise FileNotFoundError(f"{path}: storage file not initialized")
        else:
            tables[entity] = []
    _check_references(
        tables["tasks"], tables["categories"], tables["tags"],
        tables["task_tags"], tables["subtasks"],
    )
    return tables


def _save_entity(records: list[Record], filepath: PathLike, entity: str) -> None:
    _validate_records(records, entity)
    path = _entity_path(filepath, entity)
    _read_store(path.parent, candidate_entity=entity, candidate=records)
    _atomic_write(records, path, entity)


def initialize_v2_storage(data_dir: PathLike = DEFAULT_DATA_DIR) -> None:
    """Initialize V1 + five V2 files; validate existing files/references first.

    Return None. Never overwrite records or repair corrupt data. Missing tables
    count as empty during preflight, so unresolved legacy category IDs fail.
    This is not a transaction: IO failure can leave some header files created;
    retry is safe. Common module exceptions propagate.
    """
    directory = Path(data_dir)
    _read_store(directory)
    directory.mkdir(parents=True, exist_ok=True)
    storage.initialize_storage(directory / "tasks.csv")
    for entity in V2_FIELDS:
        path = _entity_path(directory / f"{entity}.csv", entity)
        try:
            stream = path.open("x", encoding="utf-8", newline="")
        except FileExistsError:
            _load_entity(path, entity)
        else:
            try:
                with stream:
                    _write_stream([], stream, entity)
            except (OSError, UnicodeError, csv.Error):
                path.unlink(missing_ok=True)
                raise


def load_categories(filepath: PathLike = DEFAULT_CATEGORIES_PATH) -> list[Record]:
    """Read existing categories sorted by (position, id); missing file raises OSError."""
    return sorted(_load_entity(_entity_path(filepath, "categories"), "categories"), key=lambda row: (row["position"], row["id"]))


def save_categories(categories: list[Record], filepath: PathLike = DEFAULT_CATEGORIES_PATH) -> None:
    """Validate candidate store and atomically save categories; reject referenced deletions."""
    _save_entity(categories, filepath, "categories")


def load_tags(filepath: PathLike = DEFAULT_TAGS_PATH) -> list[Record]:
    """Read existing valid tags in CSV order; return list[dict], no initialization."""
    return _load_entity(_entity_path(filepath, "tags"), "tags")


def save_tags(tags: list[Record], filepath: PathLike = DEFAULT_TAGS_PATH) -> None:
    """Validate candidate store and atomically save tags; reject referenced deletions."""
    _save_entity(tags, filepath, "tags")


def load_task_tags(filepath: PathLike = DEFAULT_TASK_TAGS_PATH) -> list[Record]:
    """Read existing relation rows in CSV order; validate schema/pairs, no foreign-key IO."""
    return _load_entity(_entity_path(filepath, "task_tags"), "task_tags")


def save_task_tags(task_tags: list[Record], filepath: PathLike = DEFAULT_TASK_TAGS_PATH) -> None:
    """Validate full candidate store including foreign keys; atomically save relations."""
    _save_entity(task_tags, filepath, "task_tags")


def load_subtasks(filepath: PathLike = DEFAULT_SUBTASKS_PATH) -> list[Record]:
    """Read existing valid subtasks in CSV order; no cross-file checks or initialization."""
    return _load_entity(_entity_path(filepath, "subtasks"), "subtasks")


def save_subtasks(subtasks: list[Record], filepath: PathLike = DEFAULT_SUBTASKS_PATH) -> None:
    """Validate full candidate store including parent tasks; atomically save subtasks."""
    _save_entity(subtasks, filepath, "subtasks")


def load_events(filepath: PathLike = DEFAULT_EVENTS_PATH) -> list[Record]:
    """Read existing valid Events in CSV order; no initialization. Return list[dict]."""
    return _load_entity(_entity_path(filepath, "events"), "events")


def save_events(events: list[Record], filepath: PathLike = DEFAULT_EVENTS_PATH) -> None:
    """Validate full candidate store and atomically save independent Events."""
    _save_entity(events, filepath, "events")


def validate_store_integrity(data_dir: PathLike = DEFAULT_DATA_DIR) -> None:
    """Read-only full schema/reference check; return None. Missing files raise FileNotFoundError."""
    _read_store(Path(data_dir), require_all=True)


def _query_store(task_id: str, data_dir: PathLike) -> dict[str, list[Record]]:
    _text(task_id, "task_id")
    tables = _read_store(Path(data_dir), require_all=True)
    if task_id not in {task["id"] for task in tables["tasks"]}:
        raise ValueError(f"tasks.csv: task {task_id!r} does not exist")
    return tables


def get_subtasks_for_task(task_id: str, data_dir: PathLike = DEFAULT_DATA_DIR) -> list[Record]:
    """Return target task's subtasks sorted by (position, id); reject nonexistent tasks."""
    tables = _query_store(task_id, data_dir)
    return sorted(
        (row for row in tables["subtasks"] if row["task_id"] == task_id),
        key=lambda row: (row["position"], row["id"]),
    )


def get_tag_ids_for_task(task_id: str, data_dir: PathLike = DEFAULT_DATA_DIR) -> list[str]:
    """Return target task's tag IDs sorted lexicographically; reject nonexistent tasks."""
    tables = _query_store(task_id, data_dir)
    return sorted(row["tag_id"] for row in tables["task_tags"] if row["task_id"] == task_id)


def get_tasks_for_category(category_id: str, data_dir: PathLike = DEFAULT_DATA_DIR) -> list[Record]:
    """Return category tasks in CSV order; reject nonexistent categories or invalid stores."""
    _text(category_id, "category_id")
    tables = _read_store(Path(data_dir), require_all=True)
    if category_id not in {row["id"] for row in tables["categories"]}:
        raise ValueError(f"categories.csv: category {category_id!r} does not exist")
    return [task for task in tables["tasks"] if task["category_id"] == category_id]


def calculate_task_progress(task: Record, subtasks: list[Record]) -> int:
    """Pure validated progress: completed=100, manual=value, subtasks=half-up percent.

    Validate all supplied subtasks, including unrelated rows, but count only rows
    for task.id. Does not perform IO or mutate input. Invalid data raises
    TypeError/ValueError; unrelated task existence cannot be checked in this API.
    """
    storage.validate_tasks([task])
    validate_subtasks(subtasks)
    if task["completed"]:
        return 100
    if task["progress_mode"] == "manual":
        return task["manual_progress"]
    own = [row for row in subtasks if row["task_id"] == task["id"]]
    total = len(own)
    if total == 0:
        return 0
    completed = sum(row["completed"] for row in own)
    # Exact integer equivalent of floor(completed / total * 100 + 0.5).
    return (200 * completed + total) // (2 * total)
