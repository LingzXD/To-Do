# ClearTask V2 数据存储正式契约

## 范围及 V1 兼容性

Python 3.10+，标准库，面向单进程、单写入者的本地 CSV 存储。
`storage.py` 继续提供 V1 Task 接口；`storage_v2.py` 提供五种新增实体、关系检查、查询和有效进度计算。
私有 `_storage_refs.py` 仅共享严格 CSV 读取、表头及引用检查，避免两个公开模块循环导入。
不创建 storage/ 包，不实现 Flask 路由、业务服务、前端、提醒、重复任务、账号或数据库。

V1 的 Task 12 字段、表头顺序、类型、UTF-8 编码、函数签名和基本导入导出语义不变。
V1 仅增加两项保护：已有 V2 引用文件时，保存任务前检查引用；新建表头发生 IO 错误时清理本次新建文件。
尚无 V2 引用文件的目录保持纯 V1 行为，包括不校验 category_id 外键。
V1 历史契约见 [storage_contract.md](storage_contract.md)；启用 V2 后，以本文的关联规则为补充。

## 文件和路径

```text
storage.py
storage_v2.py
_storage_refs.py
data/
  .gitkeep
  tasks.csv
  categories.csv
  tags.csv
  task_tags.csv
  subtasks.csv
  events.csv
tests/test_storage.py
tests/test_storage_v2.py
docs/storage_contract.md
docs/storage_v2_contract.md
docs/storage_v2_handoff.md
```

上图 CSV 为运行时文件，开发和测试不会创建正式用户 CSV；Git 忽略 data/*.csv 和 .cleartask-*.tmp/.bak。
默认目录由 `Path(__file__).resolve().parent / "data"` 推导，不依赖当前终端目录。
默认路径常量分别为 DEFAULT_CATEGORIES_PATH、DEFAULT_TAGS_PATH、DEFAULT_TASK_TAGS_PATH、
DEFAULT_SUBTASKS_PATH、DEFAULT_EVENTS_PATH，指向 DEFAULT_DATA_DIR 下的同名文件。

为使整表保存与 V1 引用保护指向同一数据集，**V2 filepath 必须使用规定文件名**，可自由指定父目录。
例如 `save_tags(tags, Path("sandbox") / "tags.csv")` 合法，`sandbox/custom.csv` 不合法。
V2 启用后 V1 保存任务也必须使用同目录的 tasks.csv；纯 V1 的自定义文件名仍可使用。
V2 不支持把单个实体文件设置为符号链接；目录应由应用层选定，不直接接受网页提交的任意路径。

## 数据模型与 CSV 顺序

所有实体内存表示均为 `list[dict[str, object]]`，V2 使用 Record 类型别名。
每条记录必须给出所有字段，未知字段会报错，以下默认值仅是业务层构造记录的约定；存储层不自动补值。
ID 与 created_at 均由业务层生成，不改写。ID 在**所属实体的整个表内**唯一，而不是仅在同一任务内唯一；
不同实体使用独立 ID 命名空间，建议分别使用 cat-/tag-/sub-/event- 前缀或 UUID。
名称规范化仅用于唯一性比较，文件中保留原文，ID 也不自动 trim/casefold。

### Category：categories.csv

```text
id,name,color,position
```

| 字段 | Python 类型 | 默认值 | 约束 |
|---|---|---|---|
| id | str | 业务层生成 | 去除首尾空白后非空，全表唯一 |
| name | str | 必填 | 去除首尾空白后非空；strip().casefold() 后不可重复 |
| color | str 或 None | None | 非空时严格 #RRGGBB，十六进制大小写均可，空字符串非法 |
| position | int | 0 | 非负，不接受 bool/float，可重复 |

load_categories 返回按 (position, id) 升序排序的记录；保存保持传入顺序，不静默改写文件排序。
一个 Category 可以没有 Task；一个 Task 最多通过 category_id 引用一个 Category。

### Tag：tags.csv

```text
id,name,color
```

| 字段 | Python 类型 | 默认值 | 约束 |
|---|---|---|---|
| id | str | 业务层生成 | 去除首尾空白后非空，全表唯一 |
| name | str | 必填 | 去除首尾空白后非空；strip().casefold() 后不可重复 |
| color | str 或 None | None | 非空时 #RRGGBB；空字符串非法 |

Tag 与 Category 独立，不将标签字符串列表塞入 Task 单元格。Tag 可以不被任何 Task 使用。

### TaskTag：task_tags.csv

```text
task_id,tag_id
```

| 字段 | Python 类型 | 默认值 | 约束 |
|---|---|---|---|
| task_id | str | 必填 | 去除首尾空白后非空，引用 Task.id |
| tag_id | str | 必填 | 去除首尾空白后非空，引用 Tag.id |

组合 (task_id, tag_id) 全表唯一。支持一个任务多个标签、一个标签多个任务；重复关联报错。

### Subtask：subtasks.csv

```text
id,task_id,title,completed,position
```

| 字段 | Python 类型 | 默认值 | 约束 |
|---|---|---|---|
| id | str | 业务层生成 | 去除首尾空白后非空，全表唯一 |
| task_id | str | 必填 | 去除首尾空白后非空，引用 Task.id |
| title | str | 必填 | 去除首尾空白后非空 |
| completed | bool | False | 真正 bool，不接受整数或字符串 |
| position | int | 0 | 非负，不接受 bool/float，可重复 |

仅一层子任务，不允许 parent_subtask_id 等额外字段，不带独立类别、标签、优先级或日期。
load_subtasks 保持 CSV 顺序；get_subtasks_for_task 按 (position, id) 升序稳定排序。

### Event：events.csv

```text
id,title,description,event_type,all_day,start_at,end_at,color,created_at
```

| 字段 | Python 类型 | 默认值 | 约束 |
|---|---|---|---|
| id | str | 业务层生成 | 去除首尾空白后非空，全表唯一 |
| title | str | 必填 | 去除首尾空白后非空 |
| description | str | 空字符串 | 可为空，支持多行文本 |
| event_type | str | 必填 | schedule 或 special |
| all_day | bool | False | 真正 bool |
| start_at | str | 必填 | 根据 all_day 使用严格日期或日期时间 |
| end_at | str | 必填 | 根据 all_day 使用严格日期或日期时间 |
| color | str 或 None | None | 非空时 #RRGGBB；空字符串非法 |
| created_at | str | 业务层生成 | YYYY-MM-DDTHH:MM:SS 本地日期时间 |

schedule 代表课堂、会议等普通日程，special 代表生日、假期等特殊日期/区间，不另建 special_dates.csv。
Event 与 Task 无强制外键。日历业务层自行合并展示，不将 Event 当成可完成 Task。

all_day=True 时边界必须为 YYYY-MM-DD，end_at **包含最后一天**，允许等于 start_at。
all_day=False 时边界必须为 YYYY-MM-DDTHH:MM:SS，end_at 严格晚于 start_at；可跨天。
边界不能混用日期和日期时间。使用真实日历检查（含闰年），拒绝时区、毫秒、空格分隔符等扩展格式。
不做 UTC 转换或静默精度调整。日历组件若要求全天事件的排他 end，应由展示适配层加一天。

## CSV 编码、加载和异常

固定表头顺序、列数均严格校验，重复/缺失/额外/乱序列报错；空字节文件报错，只有合法表头的文件为空集合。
写入无 BOM UTF-8，读取兼容 UTF-8 BOM；newline=""，标准 csv 引号规则支持中文、逗号、引号、换行。
None（color）写成空字符串；读取空单元格恢复 None。bool 写成 true/false；读取不区分大小写，
不接受 yes、1 或带空白的字符串。position 仅从十进制数字串恢复为 int。

统一异常：

| 异常 | 含义 |
|---|---|
| TypeError | 错误的 Python 容器、记录或字段类型，bool 冒充 int 等 |
| ValueError | 字段值、schema、CSV 格式、重复 ID/关系/规范名称、无效引用、危险删除、非法目标文件名 |
| FileNotFoundError | 已要求读取的数据文件尚未初始化；属于 OSError |
| OSError 及子类 | 文件读写、权限、磁盘、replace 或清理失败 |
| UnicodeError | 编码不合法；例如 UnicodeDecodeError，原异常保留 |

V2 模块级 docstring 给出共同异常约定，各公开函数遵循该约定。
记录级错误包含文件或实体、从 0 开始的 record 索引和相关字段/ID；CSV 解析/列数错误给出物理行号。
不打印错误后继续，不把失败读取伪装成空列表。引用错误包含 task/category/tag/subtask 的具体 ID。

## 公开函数真实签名与结果

`PathLike = str | os.PathLike[str]`；以下 Record 都是 dict[str, object]。

```python
initialize_v2_storage(data_dir: PathLike = DEFAULT_DATA_DIR) -> None

load_categories(filepath: PathLike = DEFAULT_CATEGORIES_PATH) -> list[Record]
save_categories(categories: list[Record], filepath: PathLike = DEFAULT_CATEGORIES_PATH) -> None
validate_categories(categories: list[Record]) -> None

load_tags(filepath: PathLike = DEFAULT_TAGS_PATH) -> list[Record]
save_tags(tags: list[Record], filepath: PathLike = DEFAULT_TAGS_PATH) -> None
validate_tags(tags: list[Record]) -> None

load_task_tags(filepath: PathLike = DEFAULT_TASK_TAGS_PATH) -> list[Record]
save_task_tags(task_tags: list[Record], filepath: PathLike = DEFAULT_TASK_TAGS_PATH) -> None
validate_task_tags(task_tags: list[Record]) -> None

load_subtasks(filepath: PathLike = DEFAULT_SUBTASKS_PATH) -> list[Record]
save_subtasks(subtasks: list[Record], filepath: PathLike = DEFAULT_SUBTASKS_PATH) -> None
validate_subtasks(subtasks: list[Record]) -> None

load_events(filepath: PathLike = DEFAULT_EVENTS_PATH) -> list[Record]
save_events(events: list[Record], filepath: PathLike = DEFAULT_EVENTS_PATH) -> None
validate_events(events: list[Record]) -> None

validate_store_integrity(data_dir: PathLike = DEFAULT_DATA_DIR) -> None
get_subtasks_for_task(task_id: str, data_dir: PathLike = DEFAULT_DATA_DIR) -> list[Record]
get_tag_ids_for_task(task_id: str, data_dir: PathLike = DEFAULT_DATA_DIR) -> list[str]
get_tasks_for_category(category_id: str, data_dir: PathLike = DEFAULT_DATA_DIR) -> list[Record]
calculate_task_progress(task: Record, subtasks: list[Record]) -> int
```

| 函数组 | 参数、返回值、检查和异常边界 |
|---|---|
| initialize_v2_storage | data_dir 为一套六表目录。先只读检查已有结构与关系，缺失文件视为空表；然后排他创建缺失文件，包括 tasks.csv。返回 None。已有数据损坏或引用不可满足则报错、不修复。创建中途 IO 失败不回滚之前创建的合法表头，但会尽可能清理本次失败的新文件，可重试。 |
| validate_categories/tags | 参数为整个实体列表，检查表内结构、类型、ID 和规范化名称唯一性。无 IO，返回 None，失败 TypeError/ValueError。 |
| validate_task_tags | 检查整个关系列表的字段及组合唯一性，不读取关联目标；返回 None，失败 TypeError/ValueError。 |
| validate_subtasks | 检查整个子任务列表的一层结构、字段、全表 ID；无外键 IO，返回 None，失败 TypeError/ValueError。 |
| validate_events | 检查整个事件列表的字段、ID、时间及颜色；无 IO，返回 None，失败 TypeError/ValueError。 |
| load_categories | 读取指定已存在文件，校验并恢复类型，返回按 position/id 排序的记录。不检查其他表；不创建文件。可抛共同异常。 |
| load_tags/task_tags/subtasks/events | 读取指定已存在文件，校验表内数据、恢复类型，返回 CSV 顺序的记录列表。不检查其他表；缺失抛 FileNotFoundError。可抛共同异常。 |
| 所有 save_* | 参数是待保存完整列表与规范 filepath。先校验该表，再读取同目录其他已存在表并校验候选集合的全部外键，最后原子替换这一个文件。缺失兄弟表视为空，存在但损坏则报错。返回 None；可抛共同异常。 |
| validate_store_integrity | 只读要求全部六个文件存在，逐表验证并检查外键。返回 None；未初始化 FileNotFoundError，已有损坏 ValueError/UnicodeError 等。不自动创建或修复。 |
| get_subtasks_for_task | 先校验整套已初始化数据集，再确认 task_id 存在；返回该任务子任务，按 position/id 排序。无子任务返回 []；Task 不存在 ValueError。 |
| get_tag_ids_for_task | 同样校验数据集与 Task，返回关联 Tag ID 的字典序列表；无标签返回 []，Task 不存在 ValueError。 |
| get_tasks_for_category | 校验数据集和 category_id，返回该类别下 Task，保持 tasks.csv 顺序；空类别返回 []，Category 不存在 ValueError。 |
| calculate_task_progress | 无 IO，校验完整 Task 与所有传入 Subtask 的字段/唯一性；返回 0–100 整数，不检查其他任务是否实际存在。非法输入 TypeError/ValueError，包括不相关但非法的子任务。 |

各查询若数据集不完整或损坏同样报错，不会返回误导性空结果。查询返回新加载的字典，无存储写入。
接口不给 ID 和默认值；创建记录由业务层完成，再调用 save_* 保存完整集合。

## 关联、删除及 V1 保护

外键如下：

```text
Task.category_id -> Category.id  （允许 None）
Subtask.task_id -> Task.id       （不允许空）
TaskTag.task_id -> Task.id       （不允许空）
TaskTag.tag_id -> Tag.id         （不允许空）
```

所有 V2 保存都使用候选新表替代旧表，与同目录其他表进行完整校验，不能通过整表保存删除被引用实体。
不自动级联删除或清空 Task.category_id。移除被引用类别前，业务层必须先明确解除相关任务引用；
移除标签前先保存移除其关联后的 task_tags；移除任务前先显式移除子任务和标签关联。
每一步单独保存，失败时停止并报告，之前的合法步骤不会被回滚；没有“保存全部”伪事务 API。

V1 save_tasks 在同目录 categories/tags/task_tags/subtasks 任一引用文件存在时，读取已有引用 CSV，
校验严格表头、列数、必要 ID 与关联目标，基于候选任务集拒绝悬空引用。
import_tasks 的 merge/replace 均调用 save_tasks，因此同样受保护；无需改动签名。
这道最小兼容保护不验证所有 V2 字段值（例如 color），完整检查请调用 validate_store_integrity；
V2 自身每次保存都执行全部已有表的数据验证。无循环导入、回调注册或隐藏全局开关。
手动编辑 CSV、删除引用表或调用私有函数不属于受保护的公开 API 使用方式。

## 有效进度

calculate_task_progress 先校验输入，即使 Task 已完成也不会忽略错误子任务。

1. completed=True：有效进度为 100。
2. 否则 progress_mode=manual：返回 manual_progress。
3. 否则 progress_mode=subtasks：只统计 task_id 等于当前 Task.id 的子任务。
4. 没有子任务返回 0；否则使用 floor(已完成数 / 总数 × 100 + 0.5)。

代码使用等价的精确整数计算 `(200 * completed_count + total) // (2 * total)`，避免浮点误差及银行家舍入。
例如 1/3=33、2/3=67、3/4=75、1/8=13。永不改写 manual_progress、progress_mode 或任何完成状态。

## 最小调用与全新初始化

以下代码只表示调用方的使用方式，不是新业务层实现：

```python
from pathlib import Path
import storage
import storage_v2 as v2

data_dir = Path("sandbox")  # 实际应用可以直接使用 v2.DEFAULT_DATA_DIR
v2.initialize_v2_storage(data_dir)  # 包括 V1 tasks.csv

v2.save_categories([
    {"id": "cat-study", "name": "学习", "color": "#3366FF", "position": 0},
], data_dir / "categories.csv")

tasks = storage.load_tasks(data_dir / "tasks.csv")
tasks.append({
    "id": "task-example", "title": "复习", "description": "",
    "priority": "medium", "completed": False, "category_id": "cat-study",
    "planned_start": None, "due_at": None, "progress_mode": "subtasks",
    "manual_progress": 20, "created_at": "2026-10-17T10:00:00", "completed_at": None,
})
storage.save_tasks(tasks, data_dir / "tasks.csv")

v2.save_tags([{"id": "tag-exam", "name": "考试", "color": None}], data_dir / "tags.csv")
v2.save_task_tags([{"task_id": "task-example", "tag_id": "tag-exam"}], data_dir / "task_tags.csv")
v2.save_subtasks([
    {"id": "sub-example", "task_id": "task-example", "title": "第一章", "completed": False, "position": 0},
], data_dir / "subtasks.csv")

subtasks = v2.get_subtasks_for_task("task-example", data_dir)
tag_ids = v2.get_tag_ids_for_task("task-example", data_dir)
progress = v2.calculate_task_progress(tasks[0], subtasks)
events = v2.load_events(data_dir / "events.csv")
v2.validate_store_integrity(data_dir)
```

业务层自行创建真正唯一的 ID 和本地时间戳；例子中的固定 ID/时间仅用于展示字段，不宜反复追加。

## V1 用户安全升级

1. 在应用关闭、没有其他写入者时，单独备份整个数据目录。
2. 使用 V1 load_tasks 验证现有 tasks.csv，保留原始文件，不执行自动迁移。
3. 检查 category_id；若已有非 None 引用，业务层应明确建立对应 Category 记录。
   可先调用 save_categories(完整类别列表, 同目录 categories.csv)，其候选数据必须覆盖所有旧引用。
   不能自动生成类别或静默清空旧任务字段。缺失类别直接初始化会失败且不改旧数据。
4. 调用 initialize_v2_storage(目录)，只创建缺失文件，不重写旧任务。
5. 调用 validate_store_integrity(目录)，确认全部六表结构、字段和引用有效，再启用业务功能。

Task schema 不变，无需数据格式迁移。若目录已有损坏或悬空数据，先由用户明确修正并备份，接口不会猜测修复。

## 安全边界、验收与后续演进

保存完整校验通过后，在目标目录创建 .cleartask-*.tmp，写入、flush、fsync、关闭后 os.replace。
写入/替换失败尽量保持旧文件并清理临时文件；操作系统拒绝清理时可能留下临时文件并报告错误。
已有目标不会先被删除或以 w 模式截断。初始化使用 x 模式创建，不覆盖已存在文件。

os.replace 不等于数据库事务，也不保证目录元数据的断电持久性。多 CSV 不提供 ACID 或自动回滚，
初始化中途失败可能留下部分合法空表；重试初始化可补齐，IO/权限恢复前仍会报错。
单进程、单写入者是明确前提；不提供锁，不承诺并发安全，多个读改写流程可能覆盖更新。
应用服务必须串行化修改；重要数据采用独立备份策略。

验收命令（仓库根目录）：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q storage.py storage_v2.py _storage_refs.py
git status -sb
git diff --check
git diff --stat
```

V1 原测试保留，V2 测试全部使用 TemporaryDirectory 或 StringIO，覆盖关联、进度、Event 边界、
升级、非法 CSV、写入/替换/fsync 故障和初始化失败重试，不写真实 data/ CSV。

task_service.py 接收并返回普通 dict/list，统一使用同一 data_dir，先建被引用对象再建引用关系，
删除时先解除依赖，UI 显示进度使用 calculate_task_progress，不自行覆盖 manual_progress。
app.py 负责表单/上传解析、请求响应、目录配置和捕获异常，存储层不接收 Flask 请求对象。
前端通过 app/service 获取序列化数据；全天 Event 的包含结束日语义由日历适配层处理。
目前不支持级联删除、多文件批量原子修改、独立新增实体导入导出、递归子任务或重复日程。

当需要多进程写入、多文件事务、高频修改或复杂查询时，可考虑 SQLite 迁移，保留公开服务接口，
使用外键、唯一索引和事务替代当前 CSV 检查。本轮不引入 SQLite；通知、重复调度、账号同步属于 V3 范围。
