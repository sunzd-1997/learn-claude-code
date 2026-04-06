#!/usr/bin/env python3
import json
import os
import re
import subprocess
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(override=True)

API_KEY = os.getenv("ANTHROPIC_API_KEY")
BASE_URL = os.getenv("ANTHROPIC_BASE_URL")
MODEL = os.getenv("MODEL_ID")
WORKDIR = Path.cwd()


def detect_repo_root(cwd: Path) -> Path | None:
    try:
        result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return None
        root = Path(result.stdout.strip())
        return root if root.exists() else None
    except Exception:
        return None


REPO_ROOT = detect_repo_root(WORKDIR) or WORKDIR

SYSTEM_PROMPT = (
    f"You are a coding agent at {WORKDIR}. "
    "Use task + worktree tools for multi-task work. "
    "For parallel or risky changes: create tasks, allocate worktree lanes, "
    "run commands in those lanes, then choose keep/remove for closeout. "
    "Use worktree_events when you need lifecycle visibility."
)


class EventBus:
    def __init__(self, event_log_path: Path):
        self.path = event_log_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("")

    def emit(self, event: str, task: dict | None = None, worktree: dict | None = None, error: str | None = None):
        payload = {"event": event, "ts": time.time(), "task": task or {}, "worktree": worktree or {}}
        if error:
            payload["error"] = error
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload) + "\n")

    def list_recent(self, limit: int = 20) -> str:
        count = max(1, min(int(limit or 20), 200))
        lines = self.path.read_text(encoding="utf-8").splitlines()
        recent = lines[-count:]
        items = []
        for line in recent:
            try:
                items.append(json.loads(line))
            except Exception:
                items.append({"event": "parse_error", "raw": line})
        return json.dumps(items, indent=2)


class TaskManager:
    def __init__(self, tasks_dir: Path):
        self.dir = tasks_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._next_id = self._max_id() + 1

    def _max_id(self) -> int:
        ids = []
        for file_path in self.dir.glob("task_*.json"):
            try:
                ids.append(int(file_path.stem.split("_")[1]))
            except Exception:
                pass
        return max(ids) if ids else 0

    def _path(self, task_id: int) -> Path:
        return self.dir / f"task_{task_id}.json"

    def _load(self, task_id: int) -> dict:
        path = self._path(task_id)
        if not path.exists():
            raise ValueError(f"Task {task_id} not found")
        return json.loads(path.read_text())

    def _save(self, task: dict):
        self._path(task["id"]).write_text(json.dumps(task, indent=2))

    def create(self, subject: str, description: str = "") -> str:
        task = {
            "id": self._next_id,
            "subject": subject,
            "description": description,
            "status": "pending",
            "owner": "",
            "worktree": "",
            "blockedBy": [],
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        self._save(task)
        self._next_id += 1
        return json.dumps(task, indent=2)

    def get(self, task_id: int) -> str:
        return json.dumps(self._load(task_id), indent=2)

    def exists(self, task_id: int) -> bool:
        return self._path(task_id).exists()

    def update(self, task_id: int, status: str = None, owner: str = None) -> str:
        task = self._load(task_id)
        if status:
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"Invalid status: {status}")
            task["status"] = status
        if owner is not None:
            task["owner"] = owner
        task["updated_at"] = time.time()
        self._save(task)
        return json.dumps(task, indent=2)

    def bind_worktree(self, task_id: int, worktree: str, owner: str = "") -> str:
        task = self._load(task_id)
        task["worktree"] = worktree
        if owner:
            task["owner"] = owner
        if task["status"] == "pending":
            task["status"] = "in_progress"
        task["updated_at"] = time.time()
        self._save(task)
        return json.dumps(task, indent=2)

    def unbind_worktree(self, task_id: int) -> str:
        task = self._load(task_id)
        task["worktree"] = ""
        task["updated_at"] = time.time()
        self._save(task)
        return json.dumps(task, indent=2)

    def list_all(self) -> str:
        tasks = [json.loads(file_path.read_text()) for file_path in sorted(self.dir.glob("task_*.json"))]
        if not tasks:
            return "No tasks."
        lines = []
        for task in tasks:
            marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}.get(task["status"], "[?]")
            owner = f" owner={task['owner']}" if task.get("owner") else ""
            worktree = f" wt={task['worktree']}" if task.get("worktree") else ""
            lines.append(f"{marker} #{task['id']}: {task['subject']}{owner}{worktree}")
        return "\n".join(lines)


TASKS = TaskManager(REPO_ROOT / ".tasks")
EVENTS = EventBus(REPO_ROOT / ".worktrees" / "events.jsonl")


class WorktreeManager:
    def __init__(self, repo_root: Path, tasks: TaskManager, events: EventBus):
        self.repo_root = repo_root
        self.tasks = tasks
        self.events = events
        self.dir = repo_root / ".worktrees"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "index.json"
        if not self.index_path.exists():
            self.index_path.write_text(json.dumps({"worktrees": []}, indent=2))
        self.git_available = self._is_git_repo()

    def _is_git_repo(self) -> bool:
        try:
            result = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=self.repo_root, capture_output=True, text=True, timeout=10)
            return result.returncode == 0
        except Exception:
            return False

    def _run_git(self, args: list[str]) -> str:
        if not self.git_available:
            raise RuntimeError("Not in a git repository. worktree tools require git.")
        result = subprocess.run(["git", *args], cwd=self.repo_root, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            message = (result.stdout + result.stderr).strip()
            raise RuntimeError(message or f"git {' '.join(args)} failed")
        return (result.stdout + result.stderr).strip() or "(no output)"

    def _load_index(self) -> dict:
        return json.loads(self.index_path.read_text())

    def _save_index(self, data: dict):
        self.index_path.write_text(json.dumps(data, indent=2))

    def _find(self, name: str) -> dict | None:
        index = self._load_index()
        for worktree in index.get("worktrees", []):
            if worktree.get("name") == name:
                return worktree
        return None

    def _validate_name(self, name: str):
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", name or ""):
            raise ValueError("Invalid worktree name. Use 1-40 chars: letters, numbers, ., _, -")

    def create(self, name: str, task_id: int = None, base_ref: str = "HEAD") -> str:
        self._validate_name(name)
        if self._find(name):
            raise ValueError(f"Worktree '{name}' already exists in index")
        if task_id is not None and not self.tasks.exists(task_id):
            raise ValueError(f"Task {task_id} not found")
        path = self.dir / name
        branch = f"wt/{name}"
        self.events.emit("worktree.create.before", task={"id": task_id} if task_id is not None else {}, worktree={"name": name, "base_ref": base_ref})
        try:
            self._run_git(["worktree", "add", "-b", branch, str(path), base_ref])
            entry = {"name": name, "path": str(path), "branch": branch, "task_id": task_id, "status": "active", "created_at": time.time()}
            index = self._load_index()
            index["worktrees"].append(entry)
            self._save_index(index)
            if task_id is not None:
                self.tasks.bind_worktree(task_id, name)
            self.events.emit("worktree.create.after", task={"id": task_id} if task_id is not None else {}, worktree={"name": name, "path": str(path), "branch": branch, "status": "active"})
            return json.dumps(entry, indent=2)
        except Exception as e:
            self.events.emit("worktree.create.failed", task={"id": task_id} if task_id is not None else {}, worktree={"name": name, "base_ref": base_ref}, error=str(e))
            raise

    def list_all(self) -> str:
        index = self._load_index()
        worktrees = index.get("worktrees", [])
        if not worktrees:
            return "No worktrees in index."
        lines = []
        for worktree in worktrees:
            suffix = f" task={worktree['task_id']}" if worktree.get("task_id") else ""
            lines.append(f"[{worktree.get('status', 'unknown')}] {worktree['name']} -> {worktree['path']} ({worktree.get('branch', '-')}){suffix}")
        return "\n".join(lines)

    def status(self, name: str) -> str:
        worktree = self._find(name)
        if not worktree:
            return f"Error: Unknown worktree '{name}'"
        path = Path(worktree["path"])
        if not path.exists():
            return f"Error: Worktree path missing: {path}"
        result = subprocess.run(["git", "status", "--short", "--branch"], cwd=path, capture_output=True, text=True, timeout=60)
        text = (result.stdout + result.stderr).strip()
        return text or "Clean worktree"

    def run(self, name: str, command: str) -> str:
        dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
        if any(item in command for item in dangerous):
            return "Error: Dangerous command blocked"
        worktree = self._find(name)
        if not worktree:
            return f"Error: Unknown worktree '{name}'"
        path = Path(worktree["path"])
        if not path.exists():
            return f"Error: Worktree path missing: {path}"
        try:
            result = subprocess.run(command, shell=True, cwd=path, capture_output=True, text=True, timeout=300)
            output = (result.stdout + result.stderr).strip()
            return output[:50000] if output else "(no output)"
        except subprocess.TimeoutExpired:
            return "Error: Timeout (300s)"

    def remove(self, name: str, force: bool = False, complete_task: bool = False) -> str:
        worktree = self._find(name)
        if not worktree:
            return f"Error: Unknown worktree '{name}'"
        self.events.emit("worktree.remove.before", task={"id": worktree.get("task_id")} if worktree.get("task_id") is not None else {}, worktree={"name": name, "path": worktree.get("path")})
        try:
            args = ["worktree", "remove"]
            if force:
                args.append("--force")
            args.append(worktree["path"])
            self._run_git(args)
            if complete_task and worktree.get("task_id") is not None:
                task_id = worktree["task_id"]
                before = json.loads(self.tasks.get(task_id))
                self.tasks.update(task_id, status="completed")
                self.tasks.unbind_worktree(task_id)
                self.events.emit("task.completed", task={"id": task_id, "subject": before.get("subject", ""), "status": "completed"}, worktree={"name": name})
            index = self._load_index()
            for item in index.get("worktrees", []):
                if item.get("name") == name:
                    item["status"] = "removed"
                    item["removed_at"] = time.time()
            self._save_index(index)
            self.events.emit("worktree.remove.after", task={"id": worktree.get("task_id")} if worktree.get("task_id") is not None else {}, worktree={"name": name, "path": worktree.get("path"), "status": "removed"})
            return f"Removed worktree '{name}'"
        except Exception as e:
            self.events.emit("worktree.remove.failed", task={"id": worktree.get("task_id")} if worktree.get("task_id") is not None else {}, worktree={"name": name, "path": worktree.get("path")}, error=str(e))
            raise

    def keep(self, name: str) -> str:
        worktree = self._find(name)
        if not worktree:
            return f"Error: Unknown worktree '{name}'"
        index = self._load_index()
        kept = None
        for item in index.get("worktrees", []):
            if item.get("name") == name:
                item["status"] = "kept"
                item["kept_at"] = time.time()
                kept = item
        self._save_index(index)
        self.events.emit("worktree.keep", task={"id": worktree.get("task_id")} if worktree.get("task_id") is not None else {}, worktree={"name": name, "path": worktree.get("path"), "status": "kept"})
        return json.dumps(kept, indent=2) if kept else f"Error: Unknown worktree '{name}'"


WORKTREES = WorktreeManager(REPO_ROOT, TASKS, EVENTS)


def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(item in command for item in dangerous):
        return "Error: Dangerous command blocked"
    try:
        result = subprocess.run(command, shell=True, cwd=WORKDIR, capture_output=True, text=True, timeout=120)
        output = (result.stdout + result.stderr).strip()
        return output[:50000] if output else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"


def run_read(path: str, limit: int = None) -> str:
    try:
        lines = safe_path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    try:
        file_path = safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        return f"Wrote {len(content)} bytes"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        file_path = safe_path(path)
        content = file_path.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        file_path.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


TOOL_HANDLERS = {
    "bash": lambda **kw: run_bash(kw["command"]),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "task_create": lambda **kw: TASKS.create(kw["subject"], kw.get("description", "")),
    "task_list": lambda **kw: TASKS.list_all(),
    "task_get": lambda **kw: TASKS.get(kw["task_id"]),
    "task_update": lambda **kw: TASKS.update(kw["task_id"], kw.get("status"), kw.get("owner")),
    "task_bind_worktree": lambda **kw: TASKS.bind_worktree(kw["task_id"], kw["worktree"], kw.get("owner", "")),
    "worktree_create": lambda **kw: WORKTREES.create(kw["name"], kw.get("task_id"), kw.get("base_ref", "HEAD")),
    "worktree_list": lambda **kw: WORKTREES.list_all(),
    "worktree_status": lambda **kw: WORKTREES.status(kw["name"]),
    "worktree_run": lambda **kw: WORKTREES.run(kw["name"], kw["command"]),
    "worktree_keep": lambda **kw: WORKTREES.keep(kw["name"]),
    "worktree_remove": lambda **kw: WORKTREES.remove(kw["name"], kw.get("force", False), kw.get("complete_task", False)),
    "worktree_events": lambda **kw: EVENTS.list_recent(kw.get("limit", 20)),
}

TOOLS = [
    {"type": "function", "function": {"name": "bash", "description": "Run a shell command in the current workspace (blocking).", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read file contents.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Write content to file.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "edit_file", "description": "Replace exact text in file.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}}},
    {"type": "function", "function": {"name": "task_create", "description": "Create a new task on the shared task board.", "parameters": {"type": "object", "properties": {"subject": {"type": "string"}, "description": {"type": "string"}}, "required": ["subject"]}}},
    {"type": "function", "function": {"name": "task_list", "description": "List all tasks with status, owner, and worktree binding.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "task_get", "description": "Get task details by ID.", "parameters": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}}},
    {"type": "function", "function": {"name": "task_update", "description": "Update task status or owner.", "parameters": {"type": "object", "properties": {"task_id": {"type": "integer"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}, "owner": {"type": "string"}}, "required": ["task_id"]}}},
    {"type": "function", "function": {"name": "task_bind_worktree", "description": "Bind a task to a worktree name.", "parameters": {"type": "object", "properties": {"task_id": {"type": "integer"}, "worktree": {"type": "string"}, "owner": {"type": "string"}}, "required": ["task_id", "worktree"]}}},
    {"type": "function", "function": {"name": "worktree_create", "description": "Create a git worktree and optionally bind it to a task.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "task_id": {"type": "integer"}, "base_ref": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "worktree_list", "description": "List worktrees tracked in .worktrees/index.json.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "worktree_status", "description": "Show git status for one worktree.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "worktree_run", "description": "Run a shell command in a named worktree directory.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "command": {"type": "string"}}, "required": ["name", "command"]}}},
    {"type": "function", "function": {"name": "worktree_remove", "description": "Remove a worktree and optionally mark its bound task completed.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "force": {"type": "boolean"}, "complete_task": {"type": "boolean"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "worktree_keep", "description": "Mark a worktree as kept in lifecycle state without removing it.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "worktree_events", "description": "List recent worktree/task lifecycle events from .worktrees/events.jsonl.", "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}}}}},
]


def call_open_api(messages: list, tools: list | None = None):
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {"model": MODEL, "messages": messages, "max_tokens": 8000, "temperature": 0.5, "stream": False}
    if tools:
        payload["tools"] = tools
    with httpx.Client(timeout=120.0) as client:
        response = client.post(f"{BASE_URL}/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


def agent_loop(messages: list):
    while True:
        response_data = call_open_api(messages, TOOLS)
        choice = response_data["choices"][0]
        message = choice["message"]
        assistant_message = {"role": "assistant", "content": message.get("content", "")}
        if "tool_calls" in message:
            assistant_message["tool_calls"] = message["tool_calls"]
        messages.append(assistant_message)
        if choice["finish_reason"] != "tool_calls":
            return
        results = []
        for tool_call in message.get("tool_calls", []):
            function_name = tool_call["function"]["name"]
            arguments = json.loads(tool_call["function"]["arguments"])
            handler = TOOL_HANDLERS.get(function_name)
            try:
                output = handler(**arguments) if handler else f"Unknown tool: {function_name}"
            except Exception as e:
                output = f"Error: {e}"
            print(f"\033[33m> {function_name}: {str(output)[:200]}\033[0m")
            results.append({"tool_call_id": tool_call["id"], "role": "tool", "content": str(output)})
        if results:
            messages.extend(results)


if __name__ == "__main__":
    print(f"Repo root for s12: {REPO_ROOT}")
    if not WORKTREES.git_available:
        print("Note: Not in a git repo. worktree_* tools will return errors.")
    history = []
    while True:
        try:
            query = input("\033[36ms12 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": query})
        agent_loop(messages)
        for message in reversed(messages):
            if message["role"] == "assistant" and message.get("content"):
                print(message["content"])
                break
        history = [message for message in messages if message["role"] != "system"]
        print()
