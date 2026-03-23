#!/usr/bin/env python3
import json
import os
import subprocess

import httpx
from dotenv import load_dotenv
from pyexpat.errors import messages
from pathlib import Path

load_dotenv(override=True)

API_KEY = os.getenv("ANTHROPIC_API_KEY")
BASE_URL = os.getenv("ANTHROPIC_BASE_URL")
MODEL = os.getenv("MODEL_ID")
WORKDIR=os.getcwd()

SYSTEM_PROMPT = f"You are a coding agent at {WORKDIR}. Use bash to solve tasks. Act, don't explain."

TOOLS = [{
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
}]

def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"path escapes workspace: {path}")
    return path

def run_read(path: str, limit: int = None) -> str:
    try:
        text = safe_path(path).read_text()
        lines = text.splitlines()
        if limit and limit <len(lines):
            lines = lines[:limit] + [f"...{len(lines) - limit} more lines"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"
        
def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"
    
def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        text = fp.read_text()
        if old_text not in text:
            return f"Error: old_text not found in {path}"
        fp.write_text(content.replace(old_text, new_text,1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


def call_open_api(messages: list, tools: list = None):
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": MODEL,
        "messages": messages,
        "mas_token": 8000,
        "temperature": 0.5,
        "stream": False,
    }

    if tools:
        payload["tools"] = tools

    try:
        with httpx.Client(timeout=120.0) as client:
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

def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown","reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command,shell=True,cwd=os.getcwd(),
                           capture_output=True,text=True,timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "no output"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    


def agent_loop(messages: list):
    while True:
        response_data= call_open_api(messages, TOOLS)
        choice = response_data["choices"][0]
        message = choice["message"]

        assistant_message = {"role": "assistant", "content": message.get("content","")}

        if "tool_calls" in message:
            assistant_message["tool_calls"] = message["tool_calls"]

        messages.append(assistant_message)

        if choice['finish_reason'] != "tool_calls":
            return

        results = []
        for tool_call in message.get("tool_calls", []):
            function_name = tool_call["function"]["name"]
            if function_name == "bash":
                arguments = json.loads(tool_call["function"]["arguments"])
                command = arguments["command"]
                print(f"\033[33m{command}\033[0m")
                output = run_bash(command)
                print(output[:200])
                results.append({
                    "tool_call_id": tool_call["id"],
                    "role": "tool",
                    "content": output
                })

        if results:
            messages.extend(results)



if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms01 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q","exit",""):
            break

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": query})

        agent_loop(messages)

        last_message = messages[-1]
        if last_message["role"] == "assistant" and last_message.get("content"):
            print(last_message["content"])

        history = [msg for msg in messages if msg["role"] != "system"]
        print()



