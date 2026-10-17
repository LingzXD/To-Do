"""ClearTask V1 CSV persistence for local, single-user applications (Python 3.10+)."""

import csv
import os
import re
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import TextIO

from _storage_refs import _protect_task_save


Task = dict[str, object]
PathLike = str | os.PathLike[str]
CSV_FIELDS = (
    "id", "title", "description", "priority", "completed", "category_id",
    "planned_start", "due_at", "progress_mode", "manual_progress",
    "created_at", "completed_at",
)
DEFAULT_TASKS_PATH = Path(__file__).resolve().parent / "data" / "tasks.csv"
ALLOWED_PRIORITIES = frozenset({"high", "medium", "low"})
ALLOWED_PROGRESS_MODES = frozenset({"manual", "subtasks"})
_OPTIONAL_FIELDS = ("category_id", "planned_start", "due_at", "completed_at")
_DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DATETIME_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")


def _validate_time(value: str, *, date_allowed: bool) -> None:
    """Check exact local ISO syntax and calendar validity without normalization."""
    if not isinstance(value, str):
        raise TypeError("expected an ISO date/time string")
    if _DATETIME_PATTERN.fullmatch(value):
        datetime.fromisoformat(value)
    elif date_allowed and _DATE_PATTERN.fullmatch(value):
        date.fromisoformat(value)
    else:
        expected = "YYYY-MM-DD or " if date_allowed else ""
        raise ValueError(f"expected {expected}YYYY-MM-DDTHH:MM:SS, got {value!r}")


def _parse_bool(value: str) -> bool:
    """Parse case-insensitive true/false; reject other spellings."""
    if value.lower() not in {"true", "false"}:
        raise ValueError(f"completed must be true or false, got {value!r}")
    return value.lower() == "true"


def _parse_optional_datetime(value: str) -> str | None:
    """Map an empty CSV cell to None, otherwise validate a local date/time."""
    if value == "":
        return None
    _validate_time(value, date_allowed=True)
    return value


def _validate_task(task: Task) -> None:
    """Validate all V1 fields, without filling defaults or altering the task."""
    if not isinstance(task, dict):
        raise TypeError("each task must be a dict")
    missing = set(CSV_FIELDS) - task.keys()
    unknown = task.keys() - set(CSV_FIELDS)
    if missing or unknown:
        raise ValueError(f"schema mismatch: missing={sorted(missing)!r}, unknown={list(unknown)!r}")
    for field in ("id", "title", "description", "priority", "progress_mode", "created_at"):
        if not isinstance(task[field], str):
            raise TypeError(f"{field} must be str")
    for field in ("id", "title"):
        if not task[field].strip():
            raise ValueError(f"{field} must not be blank")
    if task["priority"] not in ALLOWED_PRIORITIES:
        raise ValueError("priority must be high, medium or low")
    if type(task["completed"]) is not bool:
        raise TypeError("completed must be bool")
    if task["category_id"] is not None and not isinstance(task["category_id"], str):
        raise TypeError("category_id must be str or None")
    if task["progress_mode"] not in ALLOWED_PROGRESS_MODES:
        raise ValueError("progress_mode must be manual or subtasks")
    if type(task["manual_progress"]) is not int:
        raise TypeError("manual_progress must be int, not bool")
    if not 0 <= task["manual_progress"] <= 100:
        raise ValueError("manual_progress must be between 0 and 100")
    for field in ("planned_start", "due_at", "created_at", "completed_at"):
        value = task[field]
        if value is None and field != "created_at":
            continue
        try:
            _validate_time(value, date_allowed=field in {"planned_start", "due_at"})
        except (TypeError, ValueError) as exc:
            raise type(exc)(f"{field}: {exc}") from exc
    if not task["completed"] and task["completed_at"] is not None:
        raise ValueError("completed_at must be None when completed is False")


def validate_tasks(tasks: list[Task]) -> None:
    """Validate a full V1 task list without mutation.

    Return None. Raise TypeError for incorrect Python types or ValueError for
    invalid fields/schema, state inconsistencies and duplicate IDs. Errors
    include the task index (zero-based) and, when available, its ID.
    """
    if not isinstance(tasks, list):
        raise TypeError("tasks must be a list")
    seen: set[str] = set()
    for index, task in enumerate(tasks):
        task_id = task.get("id") if isinstance(task, dict) else None
        try:
            _validate_task(task)
            if task_id in seen:
                raise ValueError(f"duplicate ID {task_id!r}")
            seen.add(task_id)
        except (TypeError, ValueError) as exc:
            raise type(exc)(f"task[{index}] id={task_id!r}: {exc}") from exc


def _row_to_task(row: dict[str, str]) -> Task:
    """Convert CSV scalar types explicitly; validation follows at list level."""
    task: Task = dict(row)
    task["completed"] = _parse_bool(row["completed"])
    if not re.fullmatch(r"[0-9]+", row["manual_progress"]):
        raise ValueError("manual_progress must contain decimal integer digits")
    task["manual_progress"] = int(row["manual_progress"])
    task["category_id"] = row["category_id"] or None
    for field in ("planned_start", "due_at", "completed_at"):
        try:
            task[field] = _parse_optional_datetime(row[field])
        except (TypeError, ValueError) as exc:
            raise type(exc)(f"{field}: {exc}") from exc
    return task


def _task_to_row(task: Task) -> dict[str, str]:
    """Serialize an already validated task using canonical CSV scalars."""
    row = {field: str(task[field]) for field in CSV_FIELDS}
    row["completed"] = "true" if task["completed"] else "false"
    for field in _OPTIONAL_FIELDS:
        if task[field] is None:
            row[field] = ""
    return row


def _read_stream(stream: TextIO) -> list[Task]:
    """Read strict CSV from the stream's current position; do not close it."""
    reader = csv.DictReader(stream, strict=True)
    tasks: list[Task] = []
    try:
        header = reader.fieldnames
        if header:
            header[0] = header[0].removeprefix("\ufeff")
        if header != list(CSV_FIELDS):
            raise ValueError(f"CSV header must exactly match {list(CSV_FIELDS)!r}; got {header!r}")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"CSV line {reader.line_num}: incorrect number of columns")
            try:
                tasks.append(_row_to_task(row))
            except (TypeError, ValueError) as exc:
                raise type(exc)(f"CSV line {reader.line_num}, id={row.get('id')!r}: {exc}") from exc
    except csv.Error as exc:
        raise ValueError(f"CSV line {reader.line_num}: malformed CSV: {exc}") from exc
    validate_tasks(tasks)
    return tasks


def _read_path(filepath: PathLike) -> list[Task]:
    """Read an existing UTF-8/BOM CSV, preserving contextual errors."""
    path = Path(filepath)
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        try:
            return _read_stream(stream)
        except UnicodeError:
            # UnicodeDecodeError needs structured constructor arguments; preserve it.
            raise
        except (TypeError, ValueError) as exc:
            raise type(exc)(f"{path}: {exc}") from exc


def _write_stream(tasks: list[Task], stream: TextIO) -> None:
    """Write a complete CSV, leaving ownership of the stream to the caller."""
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
    writer.writeheader()
    writer.writerows(_task_to_row(task) for task in tasks)


def _atomic_write(tasks: list[Task], path: Path) -> None:
    """Write beside the target and replace only after successful flush/close."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent,
            prefix=".cleartask-", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            _write_stream(tasks, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def initialize_storage(filepath: PathLike = DEFAULT_TASKS_PATH) -> None:
    """Create parent directories and a header-only CSV if missing; return None.

    Existing files are fully validated and never overwritten. Raise ValueError
    or TypeError for invalid existing data, OSError for filesystem failures,
    and UnicodeError for invalid UTF-8. No task defaults are generated.
    """
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        stream = path.open("x", encoding="utf-8", newline="")
    except FileExistsError:
        _read_path(path)
    else:
        try:
            with stream:
                _write_stream([], stream)
        except (OSError, UnicodeError, csv.Error):
            # Only this call's exclusively created file may be removed.
            path.unlink(missing_ok=True)
            raise


def load_tasks(filepath: PathLike = DEFAULT_TASKS_PATH) -> list[Task]:
    """Load validated tasks; initialize a missing storage CSV, including custom paths.

    Return list[dict[str, object]]. Raise ValueError/TypeError for invalid CSV
    data, UnicodeError for encoding failures, or OSError for filesystem errors.
    Existing corrupt data is never replaced or silently treated as empty.
    """
    path = Path(filepath)
    if not path.exists():
        initialize_storage(path)
    return _read_path(path)


def save_tasks(tasks: list[Task], filepath: PathLike = DEFAULT_TASKS_PATH) -> None:
    """Validate and replace the entire task collection using a nearby temporary CSV.

    Return None. Raise TypeError/ValueError before any write for invalid input,
    or OSError/UnicodeError for write failures. A failed write/replace preserves
    the old target and cleans up the temporary file where possible. This is
    single-user storage, without multi-process locking or database transactions.
    When sibling V2 reference CSVs exist, also reject dangling category, subtask
    or tag references. V2 datasets use the canonical tasks.csv filename.
    """
    validate_tasks(tasks)
    path = Path(filepath)
    _protect_task_save(tasks, path)
    _atomic_write(tasks, path)


def import_tasks(
    source: PathLike | TextIO,
    filepath: PathLike = DEFAULT_TASKS_PATH,
    *,
    mode: str = "merge",
) -> int:
    """Import a path or caller-owned text stream, returning the imported row count.

    merge rejects all duplicate IDs; replace replaces the full target only after
    validating the full source. Streams are read from their current position and
    stay open. A missing source raises FileNotFoundError and is never created.
    Raise ValueError for unknown mode/conflicts/invalid CSV, TypeError for invalid
    types, UnicodeError for encoding failures, OSError for IO failures. Source or
    merge validation failure leaves the target untouched, even if it is absent.
    """
    if mode not in {"merge", "replace"}:
        raise ValueError("mode must be merge or replace")
    incoming = _read_path(source) if isinstance(source, (str, os.PathLike)) else _read_stream(source)
    path = Path(filepath)
    existing = _read_path(path) if mode == "merge" and path.exists() else []
    combined = existing + incoming
    validate_tasks(combined)
    save_tasks(combined, path)
    return len(incoming)


def export_tasks(
    destination: PathLike | TextIO,
    filepath: PathLike = DEFAULT_TASKS_PATH,
) -> None:
    """Export valid existing storage to a path or caller-owned writable text stream.

    Return None. Path output uses safe replacement; exporting to the storage path
    (including symlink/hard-link aliases) raises ValueError. Streams stay open,
    write at the current position and may contain partial output on failure.
    Raise ValueError/TypeError for invalid source data, UnicodeError for encoding
    failures, OSError for IO failures (including a missing storage source).
    Caller-supplied stream errors propagate. Existing storage is never modified.
    """
    source_path = Path(filepath)
    if isinstance(destination, (str, os.PathLike)):
        target = Path(destination)
        if target.resolve() == source_path.resolve() or (
            target.exists() and source_path.exists() and target.samefile(source_path)
        ):
            raise ValueError("export destination must differ from storage path")
        tasks = _read_path(source_path)
        _atomic_write(tasks, target)
    else:
        tasks = _read_path(source_path)
        _write_stream(tasks, destination)
