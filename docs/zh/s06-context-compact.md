# s06: Context Compact (上下文压缩)

`s01 > s02 > s03 > s04 > s05 > [ s06 ] | s07 > s08 > s09 > s10 > s11 > s12`

> *"上下文总会满, 要有办法腾地方"* -- 三层压缩策略, 换来无限会话。

## 问题

上下文窗口是有限的。读一个 1000 行的文件就吃掉 ~4000 token; 读 30 个文件、跑 20 条命令, 轻松突破 100k token。不压缩, 智能体根本没法在大项目里干活。

## 解决方案

三层压缩, 激进程度递增:

```
Every turn:
+------------------+
| Tool call result |
+------------------+
        |
        v
[Layer 1: micro_compact]        (silent, every turn)
  Replace tool_result > 3 turns old
  with "[Previous: used {tool_name}]"
        |
        v
[Check: tokens > 50000?]
   |               |
   no              yes
   |               |
   v               v
continue    [Layer 2: auto_compact]
              Save transcript to .transcripts/
              LLM summarizes conversation.
              Replace all messages with [summary].
                    |
                    v
            [Layer 3: compact tool]
              Model calls compact explicitly.
              Same summarization as auto_compact.
```

## 工作原理

1. **第一层 -- micro_compact**: 每次 LLM 调用前, 将旧的 tool result 替换为占位符。

### 核心功能
这是一个**消息列表精简函数**，专门处理用户消息中的**工具返回结果**：只保留最近的若干条工具结果，将更早的、内容过长的工具结果替换为简洁提示，压缩消息体积。

### 关键步骤
1. **收集工具结果**
   遍历消息列表，筛选出**角色为用户**、且内容是列表格式的消息；再从消息内容里，找到类型为`tool_result`（工具返回结果）的字典，记录它的位置和内容。

2. **判断是否需要精简**
   如果收集到的工具结果数量 ≤ 设定的保留数量（`KEEP_RECENT`），直接返回原消息列表，不做处理。

3. **精简旧工具结果**
   若工具结果过多，只保留**最近的N条**；对更早的工具结果：如果内容长度超过100，就把长内容替换为简洁提示`[Previous: used {tool_name}]`。

4. **返回结果**
   函数是**原地修改**原消息列表，最终返回处理后的列表。

### 核心知识点
- `enumerate`：同时获取列表的**索引**和**元素**
- `isinstance`：判断数据类型，避免类型错误
- 字典/列表操作：精准定位并修改嵌套的工具结果内容

#### 总结
1. 作用：精简用户消息里的工具返回结果，压缩消息长度
2. 逻辑：保留最近N条，旧的长内容替换为简写
3. 特点：原地修改原列表，仅针对用户消息中的工具结果处理

```python
def micro_compact(messages: list) -> list:
    # 遍历消息列表，精准找出用户发送的消息中，包含工具返回结果的内容，并记录其位置 + 内容到tool_results列表。
    tool_results = []
    # enumerate 遍历可迭代对象（列表、字符串、元组等）时，同时获取「元素的索引」和「元素本身」
    for i, msg in enumerate(messages):
        # isinstance: 判断一个对象是否属于指定的类（或多个类中的一个），返回布尔值（True/False）
        if msg["role"] == "user" and isinstance(msg.get("content"), list):
            for j, part in enumerate(msg["content"]):
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    tool_results.append((i, j, part))
    if len(tool_results) <= KEEP_RECENT:
        return messages
    for _, _, part in tool_results[:-KEEP_RECENT]:
        if len(part.get("content", "")) > 100:
            part["content"] = f"[Previous: used {tool_name}]"
    return messages
```

2. **第二层 -- auto_compact**: token 超过阈值时, 保存完整对话到磁盘, 让 LLM 做摘要。

```python
def auto_compact(messages: list) -> list:
    # Save transcript for recovery
    transcript_path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with open(transcript_path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg, default=str) + "\n")
    # LLM summarizes
    response = client.messages.create(
        model=MODEL,
        messages=[{"role": "user", "content":
            "Summarize this conversation for continuity..."
            + json.dumps(messages, default=str)[:80000]}],
        max_tokens=2000,
    )
    return [
        {"role": "user", "content": f"[Compressed]\n\n{response.content[0].text}"},
        {"role": "assistant", "content": "Understood. Continuing."},
    ]
```

3. **第三层 -- manual compact**: `compact` 工具按需触发同样的摘要机制。

4. 循环整合三层:

```python
def agent_loop(messages: list):
    while True:
        micro_compact(messages)                        # Layer 1
        if estimate_tokens(messages) > THRESHOLD:
            messages[:] = auto_compact(messages)       # Layer 2
        response = client.messages.create(...)
        # ... tool execution ...
        if manual_compact:
            messages[:] = auto_compact(messages)       # Layer 3
```

完整历史通过 transcript 保存在磁盘上。信息没有真正丢失, 只是移出了活跃上下文。

## 相对 s05 的变更

| 组件           | 之前 (s05)       | 之后 (s06)                     |
|----------------|------------------|--------------------------------|
| Tools          | 5                | 5 (基础 + compact)             |
| 上下文管理     | 无               | 三层压缩                       |
| Micro-compact  | 无               | 旧结果 -> 占位符               |
| Auto-compact   | 无               | token 阈值触发                 |
| Transcripts    | 无               | 保存到 .transcripts/           |

## 试一试

```sh
cd learn-claude-code
python agents/s06_context_compact.py
```

试试这些 prompt (英文 prompt 对 LLM 效果更好, 也可以用中文):

1. `Read every Python file in the agents/ directory one by one` (观察 micro-compact 替换旧结果)
2. `Keep reading files until compression triggers automatically`
3. `Use the compact tool to manually compress the conversation`
