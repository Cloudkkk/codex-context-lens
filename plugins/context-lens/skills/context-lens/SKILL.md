---
name: context-lens
description: 自动启用、停止或修复 Codex 消息操作栏中的上下文悬停面板，无需独立启动器。仅处理面板维护，不用于聊天查询 token 明细。
---

插件入口是消息下方操作栏最右侧的 16px SVG 饼图图标与百分比按钮，默认隐藏，回复 hover、键盘聚焦或面板打开时显示；hover 显示分类汇总（暂不支持展开明细），点击固定面板，Esc 关闭。

面板维护命令在本技能目录上两级的 `cli.py`。使用 Python 3.9+；`enable` / `start` 启动自动启用监控，`status` 查看进程和连接状态，`stop` 撤掉按钮和面板，并取消尚未执行的重开。命令优先使用 `scripts/run-python.sh` 选择应用自带 Python。启用与状态检查需使用 exec_command 的 require_escalated 权限，避免沙箱拒绝进程检查、后台监控和本机端口连接。

首次启用应执行插件根目录 `scripts/run-python.sh initialize`，使用当前 CODEX_THREAD_ID。它在本次回复和其他可见本地任务完成后自动正常退出、带参数重开；请遵循同包 setup 技能。初始化不依赖 Hook 信任，不能让用户先发送测试消息或手动 ⌘Q。不要强退或修改应用包。

`enable` / `start` 仅启动监控。`SessionStart` hook 是可选的后续自动恢复入口，需要用户审核信任；Hook 不关闭或重启应用。缺少调试端口时状态为 `initialization_required`，应由用户运行 Setup 初始化。修复请求明确包含初始化时可执行 initialize；否则先 enable、检查 status，说明实际状态。

`waiting_for_tasks` 表示重启已排队，此时简短回复并结束本轮，不能等待自己的 task_complete。`quit_cancelled`、`initialization_cancelled`、`initialization_timed_out` 或 `restart_failed` 应说明实际失败原因，不循环重启。`attached` 仅表示连接成功，未实际验证时不声称 hover 可用。

`status.windows` 包含插件内部的会话和挂载计数。`buttons > 0` 表示按钮已挂载；`diagnostics.reason` 的 `no_turn_match` 表示文案或 ID 不能唯一匹配，`no_action_rows` 表示消息操作栏结构不兼容。不要把 `attached` 当作 hover 成功。0.2.2 的实际按钮和面板已由用户确认正常；后续故障仍需核对当前计数。

保持只读：日志默认来自 `~/.codex/sessions`，不修改日志、应用包或账户设置。面板按会话 ID 和轮次 ID 匹配；不能确定对应轮次时不挂载。输入总量来自用量事件，分类仅是可见文本估算；不要把差额解释为精确工具占用。无需添加 MCP 聊天查询工具。
