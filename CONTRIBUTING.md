# Contributing

欢迎提交问题、修复与改进。讨论新功能时，请说明它如何帮助用户在面板中理解上下文。

## 本地开发

需要 Python 3.9+、Node.js 22+ 和 Git。Python 不需要第三方依赖。

```sh
git clone https://github.com/Cloudkkk/codex-context-lens.git
cd codex-context-lens
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=plugins/context-lens python3 -m unittest discover -s plugins/context-lens/tests -v
python3 -m unittest discover -s scripts/tests -v
node --test plugins/context-lens/tests/test_matching.cjs
node --check plugins/context-lens/web/overlay.js
```

打开 `面板预览.html` 可查看模拟消息中的同一份组件。需要真实接入时，在 macOS 源码目录运行：

```sh
python3 plugins/context-lens/cli.py enable
python3 plugins/context-lens/cli.py status
python3 plugins/context-lens/cli.py stop
```

真实窗口需要本地调试端口。监控不能为已运行应用补上启动参数。点击 setup 初始化入口会等待本次任务与其他可见任务完成，然后通过 NSRunningApplication 正常退出、重开；不要在仍有任务的桌面上手工执行初始化测试。

## 实现边界

- 日志必须保持只读。不要修改、移动或删除 `~/.codex/sessions`。
- 每个入口必须唯一对应会话和轮次。不要用列表顺序猜测，不能确认时跳过。
- 请求总量和分类估算需要明确区分；缓存与推理子项不重复累加。
- 不强退桌面应用，不修改应用包，不自动绕过 hook 信任。
- 原生窗口内容不应作为测试夹具提交。日志测试使用合成 JSONL。
- 不添加聊天 token 查询工具，查看入口保持在消息操作栏。

## 测试与界面验证

行为修复应增加能复现问题的测试。纯样式调整通常只需语法检查和实际交互验证，不需要重复实现本身的测试。

调整 UI 时检查：默认隐藏、回复 hover、键盘聚焦、点击固定、Esc 关闭、聊天切换、深色模式，以及虚拟列表滚动后的重新挂载。模拟预览通过不等于真实桌面挂载通过，请在 PR 中写明验证范围。

## 提交 PR

描述实际问题、变化后的行为，以及运行过的验证。避免加入真实会话内容、账户信息或个人机器路径。

发布插件行为更新时，同步修改 `plugins/context-lens/.codex-plugin/plugin.json` 与 `context_lens/__init__.py` 的版本，并更新 `CHANGELOG.md`。监控用版本号识别升级，不能只改源码而复用旧版本号。

安全问题请按 [SECURITY.md](SECURITY.md) 的方式反馈。
