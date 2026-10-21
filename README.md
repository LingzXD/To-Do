# To-Do / ClearTask

一个本地单用户待办清单：添加任务、调整优先级、标记完成、删除、按状态筛选与排序，并用 CSV 保存、导入和导出任务。Flask 页面由 `app.py` 对接 `task_service.py`（业务层）和 `storage.py`（数据层）。

## 运行

需要 Python 3.10 或更新版本。在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

然后打开 `http://127.0.0.1:5000/`。macOS/Linux 可将上述 `.venv\Scripts\python.exe` 换为 `.venv/bin/python`。

第一次访问会创建 `data/tasks.csv`，之后的修改会自动保存。该文件已在 `.gitignore` 中，不会被提交到 Git。应用只监听本机地址，面向课程演示和本地单用户使用。

## 功能与数据

- 首页显示任务及总数、未完成数、已完成数和完成率；支持按优先级、截止日期或创建时间排序，并按完成状态筛选。
- 添加任务可填写标题、描述、优先级和截止日期；其余字段由业务层生成。截止日期格式为 `YYYY-MM-DD`。
- 完成、删除和修改优先级后立即保存到 CSV。已有关联子任务或标签的任务可能被数据层拒绝删除，以维护引用完整性。
- 导出下载 `tasks.csv`。导入只接受仓库约定的 **12 字段 CSV**；`merge` 会拒绝重复 ID，`replace` 会替换现有任务。上传大小上限为 1 MB；导入失败不会覆盖旧任务。

CSV 的固定字段顺序为：

```text
id,title,description,priority,completed,category_id,planned_start,due_at,progress_mode,manual_progress,created_at,completed_at
```

详细字段类型、日期格式和导入规则见 [`docs/storage_contract.md`](docs/storage_contract.md)。

## Flask 路由

| 方法与路径 | 用途 |
|---|---|
| `GET /` | 显示任务；可传 `status=all/active/completed` 和 `sort=priority/due/created` |
| `POST /add` | 添加任务；表单字段 `title`、`description`、`priority`、`due_at` |
| `POST /complete/<task_id>` | 标记完成 |
| `POST /delete/<task_id>` | 删除任务 |
| `POST /priority/<task_id>` | 修改优先级；表单字段 `priority` |
| `GET /export` | 下载 CSV |
| `POST /import` | 上传 CSV；文件字段 `file`，导入方式 `mode=merge/replace` |

`task_id` 是 UUID 字符串。`priority` 只能是 `high`、`medium` 或 `low`。所有修改操作都使用 POST。

## 页面协作

在 A 的 `templates/index.html` 合并之前，应用使用 `templates/fallback.html` 作为可运行的简易页面。A 的模板合并后会自动优先使用，无需修改路由。模板可使用以下变量：

- `tasks`：当前筛选并排序后的任务字典列表；每条任务包含上述 12 个字段。
- `stats`：`total`、`pending`、`completed`、`rate`、`avg_progress`。
- `error`：当前操作失败时的提示，否则为 `None`。
- `status_filter`、`sort_by`：当前筛选与排序选项。

成功提示可通过 Flask 的 `get_flashed_messages()` 显示。A 的表单字段和 URL 应与上表保持一致。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

API 测试使用临时 CSV，不会改动 `data/tasks.csv`。仓库现有的存储层测试也会一起运行。
