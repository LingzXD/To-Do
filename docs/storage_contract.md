# ClearTask Storage Layer V1 接口契约

本模块兼容 Python 3.10+，仅使用标准库。所有数据层实现集中在根目录 `storage.py`。
负责任务集合验证、CSV 初始化、加载、完整保存、导入和导出。不实现任务增删、完成状态切换、
排序、逾期判断、子任务管理、Flask 路由、页面、登录或数据库。

## 1. Task V1 模型（冻结）

内存格式为 `list[dict[str, object]]`，代码中别名为 `list[Task]`。
所有 12 个字段都必须存在；未知字段会报错。表中默认值由**业务层**创建任务时设置，
数据层不补字段、不生成 ID 或时间戳，也不修改有效字符串。

| 字段（同时为固定 CSV 顺序） | Python 类型 | 业务层默认值 | 约束 |
|---|---|---|---|
| id | str | 无，业务层生成 | 去除首尾空白后非空，集合内唯一；存储时保留原文 |
| title | str | 无，用户提供 | 去除首尾空白后非空；存储时保留原文 |
| description | str | `""` | 可为空，支持中文、逗号、引号和换行 |
| priority | str | `"medium"` | `high` / `medium` / `low` |
| completed | bool | `False` | 必须是 bool，不接受 0/1 或字符串 |
| category_id | str 或 None | `None` | 不检查类别外键；CSV 空字符串加载为 None |
| planned_start | str 或 None | `None` | 严格的日期或本地日期时间 |
| due_at | str 或 None | `None` | 严格的日期或本地日期时间 |
| progress_mode | str | `"manual"` | `manual` / `subtasks` |
| manual_progress | int | `0` | 0–100，拒绝 bool 和浮点数 |
| created_at | str | 无，业务层生成 | 必填的本地日期时间，不能只含日期 |
| completed_at | str 或 None | `None` | 可选的本地日期时间；completed=False 时必须为 None |

固定 CSV 表头：

```text
id,title,description,priority,completed,category_id,planned_start,due_at,progress_mode,manual_progress,created_at,completed_at
```

日期仅接受 `YYYY-MM-DD`，日期时间仅接受 `YYYY-MM-DDTHH:MM:SS`，并验证真实日历日期。
不接受时区、毫秒、空格分隔符或精度省略；不会静默截断、转换 UTC 或改写有效值。
planned_start / due_at 可以独立为 None，不比较两者先后顺序。
日期型 due_at 表示截至当日结束，具体逾期计算由业务层负责。
completed=True 允许 completed_at=None，以兼容分阶段实现和导入。

可选时间在内存中使用 None，不能使用空字符串；CSV 中空单元格转换为 None。
category_id 允许字符串，但空字符串写入 CSV 后也会读回 None，业务层应统一使用 None 表示无类别。
布尔值写为小写 `true` / `false`，读取不区分大小写；拒绝 `yes`、`1`、带空白的布尔值等。
进度 CSV 单元格仅接受十进制数字，再转换为整数并检查范围。
读入支持 UTF-8 BOM，写出使用无 BOM 的 UTF-8。使用标准 csv 引号规则和 `newline=""`。
缺少、多余、重复或顺序不同的表头均会被拒绝；数据行列数也必须一致。

进度模式只存储：subtasks 模式不计算子任务进度，切换模式不清除 manual_progress，
completed=True 也不会自动把进度设为 100。显示进度由业务层决定。

## 2. 常量和公开函数

`CSV_FIELDS` 为上述有序元组；`ALLOWED_PRIORITIES`、`ALLOWED_PROGRESS_MODES` 为只读允许值集合。
`DEFAULT_TASKS_PATH = Path(__file__).resolve().parent / "data" / "tasks.csv"`，不依赖启动目录。
`PathLike = str | os.PathLike[str]`，包括 pathlib.Path。

```python
validate_tasks(tasks: list[Task]) -> None
initialize_storage(filepath: PathLike = DEFAULT_TASKS_PATH) -> None
load_tasks(filepath: PathLike = DEFAULT_TASKS_PATH) -> list[Task]
save_tasks(tasks: list[Task], filepath: PathLike = DEFAULT_TASKS_PATH) -> None
import_tasks(source: PathLike | TextIO,
             filepath: PathLike = DEFAULT_TASKS_PATH, *, mode: str = "merge") -> int
export_tasks(destination: PathLike | TextIO,
             filepath: PathLike = DEFAULT_TASKS_PATH) -> None
```

| 函数 | 参数、结果和行为 |
|---|---|
| validate_tasks | 检查整个列表、所有字段类型/约束和唯一 ID，不修改输入；成功返回 None。错误带任务索引（从 0 起）和 ID。 |
| initialize_storage | filepath 为正式存储位置。不存在则创建目录及仅有表头的 CSV；存在则完整校验，不覆盖、不修复；返回 None。 |
| load_tasks | 从 filepath 返回新任务列表。缺失时初始化，包括显式传入的自定义存储路径；损坏时直接报错，不返回伪造的空列表。 |
| save_tasks | 先验证整个 tasks，再完整替换 filepath。空列表合法，仍写表头；返回 None。不是追加写入。 |
| import_tasks | source 是已有来源路径或文本流。返回成功导入的来源任务条数；默认 merge，另支持 replace。 |
| export_tasks | 从已存在的 filepath 读取有效数据，写入 destination 路径或文本流；返回 None。缺失的正式源文件会报错，不自动创建。 |

异常约定：错误的 Python 数据类型抛出 TypeError；字段约束、schema、CSV 格式、重复 ID、
非法模式和导出同一路径抛出 ValueError。底层 csv.Error 转为带行号的 ValueError。
文件系统异常以 OSError 及其子类（如 FileNotFoundError、PermissionError）传播；
编码失败传播 UnicodeError（也是 ValueError 子类）。调用方提供的流自身异常会直接传播。
不要只打印错误后继续，也不要把读取失败当作“没有任务”。

所有 filepath / 路径型 destination 都应由应用层指定为专用数据或导出位置；
这些接口具有替换目标文件的能力，不应把 README、代码或其他个人文件作为目标路径。

## 3. 读取 → 修改 → 保存

业务层先负责生成唯一字符串 ID 和本地时间戳，并提供完整字段。例如：

```python
from datetime import datetime
from uuid import uuid4
from storage import load_tasks, save_tasks

tasks = load_tasks()
tasks.append({
    "id": str(uuid4()),
    "title": "完成课程作业",
    "description": "",
    "priority": "medium",
    "completed": False,
    "category_id": None,
    "planned_start": None,
    "due_at": "2026-10-20",
    "progress_mode": "manual",
    "manual_progress": 0,
    "created_at": datetime.now().isoformat(timespec="seconds"),
    "completed_at": None,
})
save_tasks(tasks)
reloaded = load_tasks()
assert reloaded == tasks
```

上述增添任务仅是业务层接入示例，不是 storage.py 提供的增删接口。
取消完成时，业务层应同时设 completed=False、completed_at=None，再保存完整集合。

## 4. 导入 / 导出和 Flask 接入

merge 会验证整个来源及其内部唯一性，读取并验证现有数据，检查跨集合 ID 冲突，
然后一次保存合并结果。有任何冲突则整个导入失败，旧文件字节保持不变，不做部分导入。
缺失来源不会被自动创建；来源校验失败也不会创建缺失的目标文件。
replace 验证整个来源后替换任务集合，不读取旧数据；因此可显式用于替换损坏的旧文件。
合法的仅表头来源可将集合清空，损坏或空字节来源不可以。
导入成功才返回条数；未知 mode 报错。

路径型导出也采用临时文件和安全替换，若与正式存储同路径（含符号链接解析和硬链接别名）则拒绝。
正式源数据无效时不写导出目标。流的读写从当前位置开始，不会自动 rewind、truncate 或关闭，
输入流应由调用者准备在 CSV 开始位置；输出流应由调用者准备为空流或合适的位置。
流写入失败可能已写出部分内容，不能承诺路径替换同样的原子性。

storage.py 无需导入 Flask。未来路由可以采用以下片段（仅说明接口，不构建应用）：

```python
from io import StringIO
from storage import import_tasks, export_tasks

# uploaded 为 Flask 层取得的上传文件；应用层先限制上传大小。
text = uploaded.read().decode("utf-8-sig")
with StringIO(text, newline="") as source:
    imported_count = import_tasks(source, mode="merge")

with StringIO(newline="") as destination:
    export_tasks(destination)
    download_text = destination.getvalue()
# Flask 层把 download_text 作为 UTF-8 CSV 下载响应，设置附件文件名和 MIME 类型。
```

Flask 层负责上传大小限制、选择允许的目标路径、处理表单与响应，以及将异常转换为用户可理解的错误。
不要将用户提交的任意路径直接传给存储函数。导入需要覆盖时明确传入 `mode="replace"`。

## 5. 安全写入、并发和扩展边界

保存先完整验证，再在目标同一目录创建 `.cleartask-*.tmp` 临时文件。
写入后 flush、fsync 并关闭，再通过 os.replace 替换目标；失败时尽可能保留旧文件并清理临时文件。
初始化使用排他创建，防止覆盖初始化检查期间出现的文件。
无法访问或文件系统阻止删除时，临时文件清理本身也可能报错。

os.replace **不等于完整数据库事务**，也不保证断电时目录元数据已持久化。
本版定位为本地单用户，不提供文件锁，不承诺多个进程同时修改同一 CSV 的正确性。
两次读取后的独立保存可能导致后保存者覆盖前者；单次原子替换无法解决此问题。
重要数据应采用单独备份策略，本模块不自动创建备份。CSV 不支持关系约束或多文件事务。

V1 仅保留 category_id 字符串，不引入 Category、Tag、Subtask 或 Event 的独立数据文件。
未来子任务模块自行计算进度，业务层决定显示结果；类别外键、标签关系和事件模型均须另行定义。
扩展 CSV 字段前需要协商新 schema 和显式迁移方案；当前版本拒绝未知字段，避免静默丢数据。

## 6. 验证与仓库约定

在仓库根目录运行：

```powershell
python -m unittest discover -s tests -v
python -m compileall -q storage.py
```

测试使用 TemporaryDirectory 和 StringIO；默认路径测试拦截实际写入，绝不操作正式 data/tasks.csv。
覆盖格式和字段边界、初始化、往返、导入冲突、BOM、中文和 CSV 特殊字符，以及模拟写入/替换失败。
运行环境无需第三方包。正式的 data/tasks.csv、临时文件和备份文件不提交，data/.gitkeep 保留目录。
