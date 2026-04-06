#!/usr/bin/env python3
import json
import os
import subprocess
import threading
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(override=True)

API_KEY = os.getenv("ANTHROPIC_API_KEY")
BASE_URL = os.getenv("ANTHROPIC_BASE_URL")
MODEL = os.getenv("MODEL_ID")
WORKDIR = Path.cwd()

SYSTEM_PROMPT = f"You are a coding agent at {WORKDIR}. Use background_run for long-running commands."


class BackgroundManager:
    def __init__(self):
        self.tasks = {}
        self._notification_queue = []
        self._lock = threading.Lock()

    def run(self, command: str) -> str:
        task_id = str(uuid.uuid4())[:8]
        self.tasks[task_id] = {"status": "running", "result": None, "command": command}
        thread = threading.Thread(target=self._execute, args=(task_id, command), daemon=True)
        thread.start()
        return f"Background task {task_id} started: {command[:80]}"

    def _execute(self, task_id: str, command: str):
        try:
            result = subprocess.run(command, shell=True, cwd=WORKDIR, capture_output=True, text=True, timeout=300)
            output = (result.stdout + result.stderr).strip()[:50000]
            status = "completed"
        except subprocess.TimeoutExpired:
            output = "Error: Timeout (300s)"
            status = "timeout"
        except Exception as e:
            output = f"Error: {e}"
            status = "error"
        self.tasks[task_id]["status"] = status
        self.tasks[task_id]["result"] = output or "(no output)"
        with self._lock:
            self._notification_queue.append(
                {"task_id": task_id, "status": status, "command": command[:80], "result": (output or "(no output)")[:500]}
            )

    def check(self, task_id: str = None) -> str:
        if task_id:
            task = self.tasks.get(task_id)
            if not task:
                return f"Error: Unknown task {task_id}"
            return f"[{task['status']}] {task['command'][:60]}\n{task.get('result') or '(running)'}"
        lines = [f"{task_id}: [{task['status']}] {task['command'][:60]}" for task_id, task in self.tasks.items()]
        return "\n".join(lines) if lines else "No background tasks."

    def drain_notifications(self) -> list:
        with self._lock:
            notifications = list(self._notification_queue)
            self._notification_queue.clear()
        return notifications


BG = BackgroundManager()


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
    "background_run": lambda **kw: BG.run(kw["command"]),
    "check_background": lambda **kw: BG.check(kw.get("task_id")),
}


TOOLS = [
    {"type": "function", "function": {"name": "bash", "description": "Run a shell command (blocking).", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read file contents.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Write content to file.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "edit_file", "description": "Replace exact text in file.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}}},
    {"type": "function", "function": {"name": "background_run", "description": "Run command in background thread. Returns task_id immediately.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "check_background", "description": "Check background task status. Omit task_id to list all.", "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}}}}},
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
        notifications = BG.drain_notifications()
        if notifications and messages:
            notification_text = "\n".join(f"[bg:{item['task_id']}] {item['status']}: {item['result']}" for item in notifications)
            messages.append({"role": "user", "content": f"<background-results>\n{notification_text}\n</background-results>"})
            messages.append({"role": "assistant", "content": "Noted background results."})
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
    history = []
    while True:
        try:
            query = input("\033[36ms08 >> \033[0m")
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
