# Security

## 接入范围

Context Lens 是第三方本地桌面插件。它只读本机 Codex rollout 日志，通过本机 Chrome DevTools Protocol 连接桌面窗口，在消息操作栏临时挂载 UI。

- 调试启动参数只绑定 `127.0.0.1:9333`；客户端拒绝非 loopback WebSocket 地址。
- 没有远程 MCP 服务、分析上传或 API Key 依赖。
- 不修改会话日志、应用包或登录启动项。
- Hook 需要用户在 Codex 中审核并信任。插件不会自动绕过信任机制。
- 面板渲染使用文本节点，不将日志内容当作 HTML 执行。

本机调试端口具有操作应用的能力。只绑定 loopback 限制了网络接入范围，但不是对本机其他进程的身份认证。不要把该端口转发到公网。

## 停止与移除

关闭插件开关，或在源码目录运行 `python3 plugins/context-lens/cli.py stop`，会撤掉面板并停止监控。随后可卸载插件。

停止插件不会关闭桌面端已经开放的调试端口。要恢复普通启动参数，完全退出桌面端，再正常打开。

## 报告问题

非敏感问题可通过仓库 [Issues](https://github.com/Cloudkkk/codex-context-lens/issues) 反馈。只附上经过脱敏的复现步骤、版本与必要的诊断字段。

不要提交原始 sessions、认证文件、访问令牌或包含私密聊天的截图。`status` 输出可能含有会话 ID 和本机路径，公开前请脱敏。

如果问题包含可利用的安全漏洞或敏感数据，请先使用 GitHub **Security → Advisories → Report a vulnerability** 的私密入口（若可用）。该入口不可用时，不要把敏感细节放到公开 Issue；先通过不含漏洞细节的 Issue 请求维护者提供私密渠道。
