# 模型 I/O schema 合成示例

仅复制自本轮已通过的 attempt-02 输出，**没有新增执行**。来源、SHA-256 和大小见 [MANIFEST.json](MANIFEST.json)。这不是 Teacher 数据、真实模型决策或新的任务成绩。

- [complete-multitool.jsonl](complete-multitool.jsonl)：真实 SDK 与本地 MockTransport 交互两次，第一回复包含两个工具调用，第二请求含对应 ToolMessage，最后返回助手文本。`complete=true` 只表示本次捕获闭合；公开工具由脚本世界提供。
- [sse-done-partial.jsonl](sse-done-partial.jsonl)：SDK 已读到 SSE `[DONE]` 并返回完整助手输出，但没有继续请求底层 EOF。因此响应与 archive 保守为 partial，不能用助手的 `[TASK DONE]` 把捕获覆盖升级为 complete。

`http_request.data.body` 与 response chunk 的 bytes 使用 base64、SHA-256、length 三元组；模型/工具字段是限定的结构化输出投影，不是原始 HTTP 响应字节。归档不保存认证 header、完整 URL/query 或非可见 artifact。回合身份及时间来自合成测试，不能由这些文件推断生产部署已验收。
