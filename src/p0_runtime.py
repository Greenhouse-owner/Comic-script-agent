# src/p0_runtime.py
"""
P0 层 — Runtime 地基层
提供安全路径、文件工具、任务管理、消息总线、后台任务、上下文压缩
"""

import os
import json
import uuid
import time
import subprocess
import threading
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Literal
from queue import Queue


# ============================================================
# 安全路径管理
# ============================================================

WORKDIR = Path(__file__).parent.parent / "workspace" / "comics"
WORKDIR.mkdir(parents=True, exist_ok=True)


def safe_path(p: str) -> Path:
    """将相对路径转换为绝对路径，并确保在工作目录内"""
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"⛔ Path escapes workspace: {p}")
    return path


# ============================================================
# 文件工具
# ============================================================

DANGEROUS_PATTERNS = [
    "rm -rf /", "sudo", "shutdown", "> /dev/",
    "mkfs", "dd if=", ":(){ :|:& };:",
]


def run_bash(command: str) -> dict:
    """执行 bash 命令，带安全检查"""
    for pattern in DANGEROUS_PATTERNS:
        if pattern in command:
            raise ValueError(f"⛔ Dangerous command detected: {pattern}")
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=30, cwd=str(WORKDIR)
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "Command timed out after 30 seconds", "exit_code": -1}


def run_read(file_path: str) -> str:
    """读取文件内容"""
    path = safe_path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"📄 File not found: {file_path}")
    return path.read_text(encoding="utf-8")


def run_write(file_path: str, content: str) -> dict:
    """写入文件（会覆盖已有内容）"""
    path = safe_path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {
        "success": True,
        "path": str(path),
        "bytes_written": len(content.encode("utf-8"))
    }


def run_edit(file_path: str, old_text: str, new_text: str) -> dict:
    """编辑文件：替换指定文本"""
    path = safe_path(file_path)
    content = path.read_text(encoding="utf-8")
    if old_text not in content:
        raise ValueError(f"⚠️ Text not found in {file_path}: {old_text[:50]}...")
    replacements = content.count(old_text)
    new_content = content.replace(old_text, new_text)
    path.write_text(new_content, encoding="utf-8")
    return {"success": True, "replacements": replacements}


# ============================================================
# 任务管理系统
# ============================================================

class TaskManager:
    """任务管理器：持久化到 JSON 文件"""

    def __init__(self, storage_file: str = "tasks.json"):
        self.storage_path = safe_path(storage_file)
        self.tasks: Dict[str, dict] = {}
        self._load()

    def _load(self):
        if self.storage_path.exists():
            self.tasks = json.loads(self.storage_path.read_text())

    def _save(self):
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.write_text(
            json.dumps(self.tasks, indent=2, ensure_ascii=False)
        )

    def create(self, title: str, assignee: str,
               depends_on: List[str] = None, metadata: dict = None) -> str:
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        status = "pending"
        if depends_on:
            for dep_id in depends_on:
                if dep_id not in self.tasks:
                    raise ValueError(f"依赖任务不存在: {dep_id}")
                if self.tasks[dep_id]["status"] != "done":
                    status = "blocked"
                    break
        self.tasks[task_id] = {
            "id": task_id,
            "title": title,
            "status": status,
            "assignee": assignee,
            "depends_on": depends_on or [],
            "metadata": metadata or {},
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        self._save()
        return task_id

    def update(self, task_id: str, status: str, result: dict = None):
        if task_id not in self.tasks:
            raise ValueError(f"任务不存在: {task_id}")
        self.tasks[task_id]["status"] = status
        self.tasks[task_id]["updated_at"] = datetime.now().isoformat()
        if result:
            self.tasks[task_id]["result"] = result
        if status == "done":
            self._unblock_dependents(task_id)
        self._save()

    def _unblock_dependents(self, completed_task_id: str):
        for task_id, task in self.tasks.items():
            if completed_task_id in task["depends_on"]:
                all_deps_done = all(
                    self.tasks[dep_id]["status"] == "done"
                    for dep_id in task["depends_on"]
                )
                if all_deps_done and task["status"] == "blocked":
                    self.tasks[task_id]["status"] = "pending"

    def list_for_assignee(self, assignee: str) -> List[dict]:
        result = [
            task for task in self.tasks.values()
            if task["assignee"] == assignee and task["status"] != "done"
        ]
        priority = {"in_progress": 0, "pending": 1, "blocked": 2, "error": 3}
        result.sort(key=lambda t: priority.get(t["status"], 99))
        return result

    def get(self, task_id: str) -> Optional[dict]:
        return self.tasks.get(task_id)


# ============================================================
# 待办管理系统
# ============================================================

TodoStatus = Literal["pending", "in_progress", "completed"]


class TodoManager:
    """待办事项管理器（轻量级，用于单个 Agent 的短期任务）"""

    def __init__(self):
        self.todos: List[dict] = []
        self.max_todos = 20

    def add(self, content: str, active_form: str):
        if len(self.todos) >= self.max_todos:
            raise ValueError(f"待办事项已达上限 {self.max_todos}")
        self.todos.append({
            "content": content,
            "active_form": active_form,
            "status": "pending"
        })

    def set_status(self, index: int, status: TodoStatus):
        if status == "in_progress":
            in_progress_count = sum(
                1 for todo in self.todos if todo["status"] == "in_progress"
            )
            if in_progress_count > 0:
                raise ValueError("已有正在进行的任务，请先完成它")
        self.todos[index]["status"] = status

    def get_current(self) -> Optional[dict]:
        for todo in self.todos:
            if todo["status"] == "in_progress":
                return todo
        return None

    def to_dict(self) -> List[dict]:
        return self.todos.copy()


# ============================================================
# 消息总线
# ============================================================

class MessageBus:
    """基于文件的消息总线，每个队友有一个 inbox 文件（JSONL 格式）"""

    def __init__(self, inbox_dir: str = "inboxes"):
        self.inbox_dir = safe_path(inbox_dir)
        self.inbox_dir.mkdir(parents=True, exist_ok=True)

    def send(self, from_agent: str, to_agent: str, message: dict):
        inbox_file = self.inbox_dir / f"{to_agent}.jsonl"
        msg = {
            "from": from_agent,
            "to": to_agent,
            "timestamp": time.time(),
            "message": message
        }
        with inbox_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    def read_inbox(self, agent_name: str, mark_read: bool = True) -> List[dict]:
        inbox_file = self.inbox_dir / f"{agent_name}.jsonl"
        if not inbox_file.exists():
            return []
        messages = []
        with inbox_file.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    messages.append(json.loads(line))
        if mark_read:
            inbox_file.write_text("")
        return messages


# ============================================================
# 后台任务管理
# ============================================================

class BackgroundManager:
    """后台任务管理器，使用线程池执行长时间任务"""

    def __init__(self, max_workers: int = 3):
        self.max_workers = max_workers
        self.notification_queue = Queue()
        self.running_tasks: Dict[str, threading.Thread] = {}

    def submit(self, task_id: str, func, *args, **kwargs):
        def wrapper():
            try:
                result = func(*args, **kwargs)
                self.notification_queue.put({
                    "task_id": task_id, "status": "success", "result": result
                })
            except Exception as e:
                self.notification_queue.put({
                    "task_id": task_id, "status": "error", "error": str(e)
                })
            finally:
                self.running_tasks.pop(task_id, None)

        thread = threading.Thread(target=wrapper, daemon=True)
        self.running_tasks[task_id] = thread
        thread.start()

    def drain_notifications(self) -> List[dict]:
        notifications = []
        while not self.notification_queue.empty():
            notifications.append(self.notification_queue.get())
        return notifications

    def is_running(self, task_id: str) -> bool:
        return task_id in self.running_tasks


# ============================================================
# 上下文压缩
# ============================================================

def auto_compact(messages: List[dict], threshold: int = 50000) -> List[dict]:
    """自动压缩对话历史"""
    total_chars = sum(len(str(msg.get("content", ""))) for msg in messages)
    estimated_tokens = total_chars // 4
    if estimated_tokens < threshold:
        return messages
    system_msgs = [msg for msg in messages if msg["role"] == "system"]
    recent_msgs = messages[-10:]
    compressed_summary = {
        "role": "assistant",
        "content": (
            f"[Auto-compressed: {len(messages) - 10} earlier messages]\n"
            f"总结：讨论了项目初始化、角色创建、章节编写等内容。"
        )
    }
    return system_msgs + [compressed_summary] + recent_msgs
