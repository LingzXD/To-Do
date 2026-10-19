# task_service.py
import uuid
from datetime import datetime

def add_task(tasks, title, priority="medium"):
    """添加新任务，返回更新后的任务列表"""
    # 1. 生成唯一 ID (C 要求必须是字符串)
    new_id = str(uuid.uuid4()) 
    
    # 2. 生成当前时间 (C 要求格式必须是 YYYY-MM-DDTHH:MM:SS)
    now = datetime.now().isoformat(timespec="seconds")
    
    # 3. 构造完全符合 C 要求的 12 字段字典
    new_task = {
        "id": new_id,
        "title": title,
        "description": "",
        "priority": priority,
        "completed": False,
        "category_id": None,
        "planned_start": None,
        "due_at": None,
        "progress_mode": "manual",
        "manual_progress": 0,
        "created_at": now,
        "completed_at": None
    }
    
    # 4. 放进列表
    tasks.append(new_task)
    return tasks


def complete_task(tasks, task_id):
    """将指定 ID 的任务标记为完成，返回更新后的列表"""
    now = datetime.now().isoformat(timespec="seconds")
    
    for task in tasks:
        if task["id"] == task_id:
            task["completed"] = True
            task["completed_at"] = now # C 的规矩：完成了要记录完成时间
            break
            
    return tasks


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
    completed_count = 0
    
    for task in tasks:
        if task["completed"]:
            completed_count += 1
            
    rate = int((completed_count / total) * 100) if total > 0 else 0

    return {
        "total": total,
        "completed": completed_count,
        "rate": rate
    }