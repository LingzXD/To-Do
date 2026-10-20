# task_service.py
import uuid
from datetime import datetime

def _get_current_time():
    """辅助函数：获取符合 C 要求的当前时间字符串 (YYYY-MM-DDTHH:MM:SS)"""
    return datetime.now().isoformat(timespec="seconds")


def add_task(tasks, title, description="", priority="medium", category_id=None, 
             planned_start=None, due_at=None, progress_mode="manual", manual_progress=0):
    """
    添加新任务（支持全部 12 个字段）
    除了 title 必填外，其他都有默认值。
    """
    new_task = {
        "id": str(uuid.uuid4()),       # 自动生成唯一字符串 ID
        "title": title,
        "description": description,
        "priority": priority,          # 只能是 high / medium / low
        "completed": False,            # 默认未完成
        "category_id": category_id,    # 分类 ID（可选）
        "planned_start": planned_start,# 计划开始时间（可选，格式 YYYY-MM-DD）
        "due_at": due_at,              # 截止时间（可选，格式 YYYY-MM-DD）
        "progress_mode": progress_mode,# 进度模式：manual（手动） / subtasks（子任务）
        "manual_progress": manual_progress, # 手动进度 0-100
        "created_at": _get_current_time(),  # 创建时间
        "completed_at": None           # 完成时间（未完成时为 None）
    }
    
    tasks.append(new_task)
    return tasks


def update_task(tasks, task_id, **kwargs):
    """
    更新任务的任意字段（这是一个万能修改函数）
    用法：update_task(tasks, task_id, title="新标题", priority="high")
    """
    # 可允许修改的字段列表（防止乱改 id 或 created_at）
    allowed_fields = ["title", "description", "priority", "category_id", 
                      "planned_start", "due_at", "progress_mode", "manual_progress"]
    
    for task in tasks:
        if task["id"] == task_id:
            for key, value in kwargs.items():
                if key in allowed_fields:
                    task[key] = value
                elif key == "completed":
                    task["completed"] = value
                    # 联动逻辑：如果标记完成，自动加完成时间；如果取消完成，清空完成时间
                    if value is True:
                        task["completed_at"] = _get_current_time()
                    else:
                        task["completed_at"] = None
            break # 找到任务修改完就退出循环
            
    return tasks


def complete_task(tasks, task_id):
    """将指定 ID 的任务标记为完成（实际上是调用 update_task）"""
    return update_task(tasks, task_id, completed=True)


def update_progress(tasks, task_id, progress):
    """快速更新任务的进度 (0-100)"""
    # 确保进度在 0-100 之间
    safe_progress = max(0, min(100, int(progress)))
    return update_task(tasks, task_id, manual_progress=safe_progress)


def delete_task(tasks, task_id):
    """删除指定 ID 的任务，返回更新后的列表"""
    for task in tasks:
        if task["id"] == task_id:
            tasks.remove(task)
            break
    return tasks


def get_statistics(tasks):
    """统计任务数据，返回字典"""
    total = len(tasks)
    completed_count = sum(1 for task in tasks if task["completed"])
    
    # 进阶统计：算一下平均进度
    total_progress = sum(task["manual_progress"] for task in tasks)
    avg_progress = int(total_progress / total) if total > 0 else 0

    return {
        "total": total,
        "completed": completed_count,
        "rate": int((completed_count / total) * 100) if total > 0 else 0,
        "avg_progress": avg_progress
    }