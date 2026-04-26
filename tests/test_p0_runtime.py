# tests/test_p0_runtime.py

import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from p0_runtime import (
    safe_path, WORKDIR, run_read, run_write, run_edit,
    TaskManager, TodoManager, MessageBus, BackgroundManager, auto_compact
)


def cleanup(*paths):
    """清理测试文件"""
    for p in paths:
        full = WORKDIR / p
        if full.is_file():
            full.unlink()
        elif full.is_dir():
            shutil.rmtree(full)


def test_safe_path():
    assert safe_path("test.md").name == "test.md"
    try:
        safe_path("../../../etc/passwd")
        assert False, "应该抛出异常"
    except ValueError:
        pass
    print("✅ safe_path")


def test_file_tools():
    cleanup("test.txt")
    run_write("test.txt", "Hello")
    assert run_read("test.txt") == "Hello"
    run_edit("test.txt", "Hello", "Hi")
    assert run_read("test.txt") == "Hi"
    cleanup("test.txt")
    print("✅ file_tools")


def test_task_manager():
    cleanup("test_tasks.json")
    tm = TaskManager("test_tasks.json")
    t1 = tm.create("任务1", "bot1")
    t2 = tm.create("任务2", "bot2", depends_on=[t1])
    assert tm.get(t2)["status"] == "blocked"
    tm.update(t1, "done")
    assert tm.get(t2)["status"] == "pending"
    tasks = tm.list_for_assignee("bot2")
    assert len(tasks) == 1
    cleanup("test_tasks.json")
    print("✅ TaskManager")


def test_todo_manager():
    todo = TodoManager()
    todo.add("写代码", "正在写代码")
    todo.add("测试", "正在测试")
    assert len(todo.to_dict()) == 2
    todo.set_status(0, "in_progress")
    assert todo.get_current()["content"] == "写代码"
    try:
        todo.set_status(1, "in_progress")
        assert False, "应该抛出异常"
    except ValueError:
        pass
    todo.set_status(0, "completed")
    todo.set_status(1, "in_progress")
    assert todo.get_current()["content"] == "测试"
    print("✅ TodoManager")


def test_message_bus():
    cleanup("inboxes")
    bus = MessageBus()
    bus.send("lead", "bot1", {"type": "task", "data": "hello"})
    bus.send("lead", "bot1", {"type": "task", "data": "world"})
    msgs = bus.read_inbox("bot1")
    assert len(msgs) == 2
    assert msgs[0]["from"] == "lead"
    # mark_read=True 后应该为空
    msgs2 = bus.read_inbox("bot1")
    assert len(msgs2) == 0
    # 不存在的 inbox 返回空
    assert bus.read_inbox("nobody") == []
    cleanup("inboxes")
    print("✅ MessageBus")


def test_background_manager():
    import time as t
    bg = BackgroundManager()
    bg.submit("job1", lambda: "done")
    t.sleep(0.5)
    notifications = bg.drain_notifications()
    assert len(notifications) == 1
    assert notifications[0]["status"] == "success"
    assert notifications[0]["result"] == "done"
    print("✅ BackgroundManager")


def test_auto_compact():
    # 短对话不压缩
    short = [{"role": "user", "content": "hi"}]
    assert auto_compact(short) == short
    # 长对话压缩
    long_msgs = [{"role": "user", "content": "x" * 10000} for _ in range(30)]
    result = auto_compact(long_msgs)
    assert len(result) < len(long_msgs)
    print("✅ auto_compact")


if __name__ == "__main__":
    test_safe_path()
    test_file_tools()
    test_task_manager()
    test_todo_manager()
    test_message_bus()
    test_background_manager()
    test_auto_compact()
    print("\n🎉 P0 层测试全部通过")
