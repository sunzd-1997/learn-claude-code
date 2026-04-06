#!/usr/bin/env python3
import json
import os
import subprocess
from pathlib import Path

import re
import httpx
from dotenv import load_dotenv

# 优先加载本地 .env，便于在不同环境下切换模型和网关配置。
load_dotenv(override=True)

# 运行时配置全部从环境变量读取，避免把密钥和模型参数硬编码到脚本里。
API_KEY = os.getenv("ANTHROPIC_API_KEY")
BASE_URL = os.getenv("ANTHROPIC_BASE_URL")
MODEL = os.getenv("MODEL_ID")
WORKDIR = Path.cwd()
SKILLS_DIR = WORKDIR / "skills"

class SkillLoader:
    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.skills = {}
        self._load_all()
    
    def _load_all(self):
        if not self.skills_dir.exists():
            return
        for f in sorted(self.skills_dir.rglob("SKILL.md")):
            text = f.read_text()
            meta, body = self._parse_frontmatter(text)
            name = meta.get("name", f.parent.name)
            self.skills[name] = {"meta": meta, "body": body, "path": str(f)}

    def _parse_frontmatter(self, text: str) -> tuple:
        """Parse YAML frontmatter between --- delimiters."""
        match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
        if not match:
            return {}, text
        meta = {}
        for line in match.group(1).strip().splitlines():
            if ":" in line:
                key, val = line.split(":", 1)
                meta[key.strip()] = val.strip()
        return meta, match.group(2).strip()

    def get_descriptions(self) -> str:
        """Layer 1: short descriptions for the system prompt."""
        if not self.skills:
            return "(no skills available)"
        lines = []
        for name, skill in self.skills.items():
            desc = skill["meta"].get("description", "No description")
            tags = skill["meta"].get("tags", "")
            line = f"  - {name}: {desc}"
            if tags:
                line += f" [{tags}]"
            lines.append(line)
        return "\n".join(lines)

    def get_content(self, name: str) -> str:
        """Layer 2: full content for tool execution."""
        skill = self.skills.get(name)
        if not skill:
            return f"Error: Unknown skill '{name}'. Available: {', '.join(self.skills.keys())}"
        return f"<skill name=\"{name}\">\n{skill['body']}\n</skill>"

SKILL_LOADER = SkillLoader(SKILLS_DIR)

# 系统提示词会把当前工作目录注入给模型，约束其围绕本地代码仓库执行任务。
SYSTEM_PROMPT = f"""You are a coding agent at {WORKDIR}.
Use load_skill to access specialized knowledge before tackling unfamiliar topics.

Skills available:
{SKILL_LOADER.get_descriptions()}"""

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


TOOL_HANDLERS = {
    # 把工具名称映射到本地执行函数，便于在收到 tool call 后动态分发。
    "bash": lambda **kw: run_bash(kw["command"]),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "load_skill": lambda **kw: SKILL_LOADER.get_content(kw["name"]),
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
            "name": "load_skill",
            "description": "Load specialized knowledge by name.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Skill name to load"}},
                "required": ["name"],
            },
        },
    }
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
        for tool_call in message.get("tool_calls", []):
            function_name = tool_call["function"]["name"]
            arguments = json.loads(tool_call["function"]["arguments"])
            try:
                handler = TOOL_HANDLERS.get(function_name)
                output = handler(**arguments) if handler else f"Unknown tool: {function_name}"
            except Exception as e:
                output = f"Error: {e}"

            print(f"\033[33m> {function_name}: {output[:200]}\033[0m")

            results.append(
                {
                    "tool_call_id": tool_call["id"],
                    "role": "tool",
                    "content": output,
                }
            )

        if results:
            messages.extend(results)


if __name__ == "__main__":
    # history 保存跨轮对话上下文；每轮都会重新补上 system prompt。
    history = []
    while True:
        try:
            query = input("\033[36ms04 >> \033[0m")
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
