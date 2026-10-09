# 手动安装 Context Lens

适合开发者或需要控制来源目录的用户。普通用户使用 [macOS 安装包](../INSTALL.md)。

1. 解压 `codex-context-local.zip` 到长期保留的位置，如 `~/codex-context-local`。
2. 登记来源：`codex plugin marketplace add /绝对路径/codex-context-local`。也可以让桌面端 Codex 执行，只登记来源。
3. 在插件目录选择「本地上下文面板」，安装并启用 Context Lens；或者运行 `codex plugin add context-lens@codex-context-local`。
4. 打开插件详情，点击初始化 / Run setup。需要时会等待设置回复结束，然后自动退出、重开。

首次使用无需信任 Hook。可选：在「设置 → Hooks」审核并信任 SessionStart，支持后续新建或恢复聊天时补启动监控。

该 ZIP 是 marketplace 来源目录，不能当成单插件包上传到「创建插件」。保留目录中的隐藏文件；更新时整体替换，避免遗留旧的 root plugin.json。
