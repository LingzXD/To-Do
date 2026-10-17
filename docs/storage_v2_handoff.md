# ClearTask V2 团队快速接入

先阅读 [正式接口契约](storage_v2_contract.md)。Task 继续 `import storage`，新增实体使用
`import storage_v2 as v2`。私有 `_storage_refs.py` 不供业务层调用。

## 初始化与路径

```python
from pathlib import Path
import storage
import storage_v2 as v2

data_dir = v2.DEFAULT_DATA_DIR  # 测试/沙盒请显式换成独立目录
v2.initialize_v2_storage(data_dir)  # 一次创建 V1 + V2 六个 CSV
v2.validate_store_integrity(data_dir)
```

同一数据集六表放同一目录，文件名固定；各 load_* 不创建缺失的 V2 文件。
旧用户先备份；已有 category_id 引用必须先定义对应类别，不能直接初始化后假定数据已有效。

## 创建、读取与保存

全部使用普通 list[dict]，业务层负责生成 ID、时间戳和完整字段，保存函数不填默认值。
完整字段及可运行的 Task/关联创建例子见正式契约。

```python
categories = v2.load_categories(data_dir / "categories.csv")
categories.append({"id": "cat-new", "name": "课程", "color": None, "position": 0})
v2.save_categories(categories, data_dir / "categories.csv")

tasks = storage.load_tasks(data_dir / "tasks.csv")
# task_service.py 修改完整 Task 列表后：
storage.save_tasks(tasks, data_dir / "tasks.csv")
```

Tag、TaskTag、Subtask、Event 对应 load_tags/save_tags、load_task_tags/save_task_tags、
load_subtasks/save_subtasks、load_events/save_events，调用方式一致。
每次 save 保存完整集合。先保存 Category/Task/Tag，再创建 Subtask 和 TaskTag 引用。
Category 和 Tag 名称按 strip().casefold() 唯一，但保存原文。

## 查询和进度

```python
subtasks = v2.get_subtasks_for_task(task_id, data_dir)  # 按 position/id 排序
tag_ids = v2.get_tag_ids_for_task(task_id, data_dir)    # list[str]，按 ID 排序
tasks_in_category = v2.get_tasks_for_category(category_id, data_dir)
progress = v2.calculate_task_progress(task, subtasks)  # int，不修改输入
```

整体完成返回 100；manual 返回 manual_progress；subtasks 按已完成比例四舍五入，空集合为 0。
不要为显示进度改写 manual_progress 或子任务完成状态。
查询会校验整套数据；ID 不存在报错，存在但没有关联时返回空列表。

## 错误与删除

业务层捕获 TypeError（调用数据类型）、ValueError（格式、约束、重复或引用）、
OSError（文件缺失/权限/磁盘）。UnicodeError 为 ValueError 子类，可单独提示编码错误。
不要把异常统一转换为空集合。

被引用类别/标签/任务不能直接整表删除，V1 save_tasks 和 replace 导入也受保护。
删除任务前先由业务层明确删除关联和子任务；删除标签前先移除 TaskTag；删除类别前先解除 Task 引用。
每步单独保存，无自动级联、无多文件事务或自动回滚。发生错误停止后续步骤并报告。

## app.py 与前端

app.py 负责输入解析、输出响应和统一 data_dir，不将请求对象传入存储层。
日历读取 load_events；all_day=True 的 end_at 包含最后一天，精确时间事件 end_at 必须晚于 start_at。
前端需要排他结束日期时在展示适配层转换；Event 与 Task 独立。

本版只支持单进程单写入者。并发写入、跨文件事务、级联删除、递归子任务、重复事件、通知及云同步均未实现。
需要多文件操作时不要假定失败会自动回滚；重要数据应单独备份。

测试命令：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q storage.py storage_v2.py _storage_refs.py
```
