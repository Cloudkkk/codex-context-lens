# 安装 Context Lens

需要 macOS 桌面版 Codex / ChatGPT 和 Python 3.9+。

1. 解压 `codex-context-local.zip`，将目录放到长期保留的位置。
2. 在 Codex 桌面聊天中发送下面的请求，将路径替换为实际解压目录：

   ```text
   请将以下目录注册为 Codex 本地 plugin marketplace：<codex-context-local 目录绝对路径>。
   只登记来源，不安装插件、不修改其他来源、不自动信任 hook。
   ```

   登记对应 `codex plugin marketplace add /absolute/path/to/codex-context-local`，这一步不是插件安装。
3. 重新打开桌面端，进入「插件目录」，选择来源「本地上下文面板」，安装并启用「上下文明细 · Context Lens」。
4. 在「设置 → Hooks / 钩子」点击刷新，展开「来自插件 → Context Lens」，审核并信任 `SessionStart`：

   ```sh
   python3 "${PLUGIN_ROOT}/cli.py" hook
   ```

5. 新建或恢复一个本地聊天。首次如未出现入口，等任务结束后按 ⌘Q 完全退出一次，等待插件自动重开，再打开本地聊天。
6. 等回复完成，鼠标移到回复上，再悬停操作栏最右侧的饼图图标。

不要在「创建插件」里上传这个 ZIP；当前账户上传来源不加载本机 hook。

Hooks 中没有 Context Lens 时，先确认第 3 步完成，且安装的是 `context-lens@codex-context-local`。仅登记来源不会出现 hook。

完整使用、更新和排查说明见 [README.md](README.md)。
