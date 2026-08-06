# 跨层通用指南

这些指南会与具体层的 spec 一起加载，用来处理本仓库最容易出错的边界：玩家身份、Python ↔ SDK ↔ Addon 数据流、协议分片和重复契约。

| 指南 | 使用时机 |
|---|---|
| [复用与单一契约](./code-reuse-thinking-guide.md) | 新增 helper、消息字段、分片或脱敏逻辑前 |
| [跨层数据流与契约](./cross-layer-thinking-guide.md) | 改动 Gateway、Agent、Addon、UI 或协议时 |

它们不是替代测试和项目文档的泛化建议；具体实现以源码、[`CONTEXT.md`](../../../CONTEXT.md)、[`CLAUDE.md`](../../../CLAUDE.md) 和本目录的 backend/addon spec 为准。
