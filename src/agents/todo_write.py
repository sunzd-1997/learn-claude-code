#!/usr/bin/env python3
import json
import os
import subprocess
from pathlib import Path

import httpx
from dotenv import load_dotenv

# 优先加载本地 .env，便于在不同环境下切换模型和网关配置。
load_dotenv(override=True)

# 运行时配置全部从环境变量读取，避免把密钥和模型参数硬编码到脚本里。
API_KEY = os.getenv("ANTHROPIC_API_KEY")
BASE_URL = os.getenv("ANTHROPIC_BASE_URL")
MODEL = os.getenv("MODEL_ID")
WORKDIR = Path.cwd()

# 系统提示词会把当前工作目录注入给模型，约束其围绕本地代码仓库执行任务。
SYSTEM_PROMPT = f"""You are a coding agent at {WORKDIR}.
Use the todo tool to plan multi-step tasks. Mark in_progress before starting, completed when done.
Prefer tools over prose."""


class TodoManager:
    def __init__(self):
        self.items = []

    def update(self, items: list) -> str:
        # todo 状态是模型自己维护的，因此这里要做严格校验，避免状态漂移。
        if len(items) > 20:
            raise ValueError("Max 20 todos allowed")
        validated = []
        in_progress_count = 0
        for i, item in enumerate(items):
            text = str(item.get("text", "")).strip()
            status = str(item.get("status", "pending")).lower()
            item_id = str(item.get("id", str(i + 1)))
            if not text:
                raise ValueError(f"Item {item_id}: text required")
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"Item {item_id}: invalid status '{status}'")
            if status == "in_progress":
                in_progress_count += 1
            validated.append({"id": item_id, "text": text, "status": status})
        # 同一时间只允许一个进行中的任务，强制模型给出明确的当前焦点。
        if in_progress_count > 1:
            raise ValueError("Only one task can be in_progress at a time")
        self.items = validated
        return self.render()

    def render(self) -> str:
        if not self.items:
            return "No todos."
        lines = []
        for item in self.items:
            marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}[item["status"]]
            lines.append(f"{marker} #{item['id']}: {item['text']}")
        done = sum(1 for t in self.items if t["status"] == "completed")
        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)


TODO = TodoManager()


def safe_path(p: str) -> Path:
    # 所有文件操作都被限制在当前工作区内，防止通过 ../ 访问到仓库外路径。
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_bash(command: str) -> str:
    # 这里只做了非常轻量的黑名单拦截，主要用于阻止明显危险的命令。
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        # 统一在工作区内执行命令，并限制最长执行时间，避免代理长时间卡住。
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (result.stdout + result.stderr).strip()
        return output[:50000] if output else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"


def run_read(path: str, limit: int | None = None) -> str:
    try:
        text = safe_path(path).read_text()
        lines = text.splitlines()
        # 如果调用方只想看文件前几行，则追加剩余行数提示，减少上下文占用。
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    try:
        file_path = safe_path(path)
        # 写文件前自动创建父目录，方便模型直接落盘新文件。
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        file_path = safe_path(path)
        content = file_path.read_text()
        # edit_file 只替换首次匹配，避免一次调用误改多个相同片段。
        if old_text not in content:
            return f"Error: Text not found in {path}"
        file_path.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"

# 按 OpenAI 兼容的 tool schema 描述当前代理可调用的本地工具。
TOOL_HANDLERS = {
    # 把工具名称映射到本地执行函数，便于在收到 tool call 后动态分发。
    "bash": lambda **kw: run_bash(kw["command"]),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "todo": lambda **kw: TODO.update(kw["items"]),
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace exact text in a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo",
            "description": "Update task list. Track progress on multi-step tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "text": {"type": "string"},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                },
                            },
                            "required": ["id", "text", "status"],
                        },
                    }
                },
                "required": ["items"],
            },
        },
    },
]



def call_open_api(messages: list, tools: list | None = None):
    # 请求的是兼容 chat completions 的接口，因此消息和 tools 都按该格式组织。
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": 8000,
        "temperature": 0.5,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools

    try:
        with httpx.Client(timeout=120.0) as client:
            # BASE_URL 由外部注入，便于切换官方或兼容网关。
            response = client.post(
                f"{BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        print(f"[DEBUG] API Error: {e}")
        if hasattr(e, "response"):
            print(f"[DEBUG] Response body: {e.response.text}")
        raise Exception(f"API call failed: {e}")


def agent_loop(messages: list):
    # 只要模型还在请求工具，就持续循环：调用模型 -> 执行工具 -> 回填结果。
    # 连续若干轮不更新 todo，就主动提醒模型重新同步自己的执行计划。
    rounds_since_todo = 0
    while True:
        response_data = call_open_api(messages, TOOLS)
        choice = response_data["choices"][0]
        message = choice["message"]

        # 先把 assistant 原始响应加入上下文，保证后续 tool 结果能和这次调用关联起来。
        assistant_message = {
            "role": "assistant",
            "content": message.get("content", ""),
        }
        if "tool_calls" in message:
            assistant_message["tool_calls"] = message["tool_calls"]

        messages.append(assistant_message)

        if choice["finish_reason"] != "tool_calls":
            return

        results = []
        used_todo = False
        for tool_call in message.get("tool_calls", []):
            function_name = tool_call["function"]["name"]
            arguments = json.loads(tool_call["function"]["arguments"])
            handler = TOOL_HANDLERS.get(function_name)
            try:
                output = handler(**arguments) if handler else f"Unknown tool: {function_name}"
            except Exception as e:
                output = f"Error: {e}"
            # 在终端打印简短执行结果，方便观察代理当前调用了什么工具。
            print(f"\033[33m> {function_name}: {output[:200]}\033[0m")
            results.append(
                {
                    "tool_call_id": tool_call["id"],
                    "role": "tool",
                    "content": output,
                }
            )
            if function_name == "todo":
                used_todo = True

        if results:
            messages.extend(results)
        # 一旦本轮没有更新 todo，就把计数器加一；达到阈值后注入提醒消息。
        rounds_since_todo = 0 if used_todo else rounds_since_todo + 1
        if rounds_since_todo >= 3:
            # 用 user 消息注入 reminder，能直接进入下一轮上下文而不改动 tool 协议。
            messages.append({"role": "user", "content": "<reminder>Update your todos.</reminder>"})


if __name__ == "__main__":
    # history 保存跨轮对话上下文；每轮都会重新补上 system prompt。
    history = []
    while True:
        try:
            query = input("\033[36ms02 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break

        if query.strip().lower() in ("q", "exit", ""):
            break

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": query})

        agent_loop(messages)

        # 逆序查找最后一条 assistant 文本回复，作为本轮对用户的展示内容。
        for message in reversed(messages):
            if message["role"] == "assistant" and message.get("content"):
                print(message["content"])
                break

        # system prompt 不进入持久历史，避免每轮重复堆叠。
        history = [message for message in messages if message["role"] != "system"]
        print()
