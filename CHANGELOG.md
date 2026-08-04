# Changelog

本文件手工维护完整版本历史。GitHub Releases 的 release notes 由 workflow 自动生成，与本文件分工互补。

## [2.5.0] - 2026-08-04

### 🎯 版本体系统一

- **全仓库单一版本号**：`_version.py` 为唯一真源（single source of truth），addon 与 Python 服务端同步演进
- **Addon 首次以 2.x 打版**：`package.json` 从 `0.2.0` 跳至 `2.5.0`，两个 manifest 的 `header.version` / `modules[*].version` 及 resource pack 依赖版本同步为 `[2, 5, 0]`（不再沿用旧的 `[1, 0, 0]`）
- 新增 `scripts/sync-version.sh` 一键同步脚本：支持全量同步、`--addon-only <ver>`（addon 单独热修）、`--check`（一致性校验），写入保持文件原格式
- Release workflow 更新：版本自动读取 `_version.py`（手动输入保留为覆盖）、release 名称统一为 `MCBE AI Agent v{version}`、changelog 统计全仓库提交、release 资产附带 Python 服务端源码 zip

### 📝 文档与配置

- README 全面重构（v2.5.0 标题、补全 16 个游戏内命令、修正模型名与协议名、修正配置默认值、精简结构）
- 更新日志从 README 迁移至本文件
- `config.example.json` 补入 `AGENT 连续模式` 命令，避免 `cli.py init` 后丢失
- `system_prompt` 代码默认值与 `config.example.json` 措辞统一
- addon README 替换为实际项目说明（不再使用 Microsoft 示例模板）

## [2.4.0] - 2026-06-19

- ✨ **AI 聊天广播控制**: 新增 `AGENT 广播` 命令，支持全服广播或指定玩家广播 AI 回复
- 🔧 **会话隔离增强**: 引入对话失效 epoch 机制，避免对话切换期间的竞态条件
- ⚡ **流控与发送加固**: 统一流控中间件支持 sentence mode 语义分句，WebSocket 命令投递和响应发送更加健壮
- 🛡️ **MCP 工具热重载**: 支持运行时通过命令重载 MCP 工具集，自动跳过不健康的服务器

## [2.3.1] - 2026-05-02

- 修复多人共享同一 `/wsserver` 连接时的玩家识别、上下文历史和 UI 响应串扰问题
- 将对话历史、会话锁、上下文开关、模型、模板和变量升级为 `(connection_id, player_name)` 维度隔离
- Agent Worker 改为同玩家串行、跨玩家并行处理请求
- UI 聊天路径和聊天框命令统一使用当前消息的真实 `sender`

## [2.3.0] - 2026-02-16

- ✨ **开发模式**: 新增开发模式功能，支持跳过身份验证用于本地开发调试
- 🔧 支持通过 `--dev` 命令行参数或 `config.json` 的 `dev_mode` 启用
- ⚠️ 开发模式下会显示明确的安全警告

## [2.2.1] - 2026-02-15

- 🔧 **日志控制优化**: 新增 WebSocket 和 LLM 原始日志开关配置，支持按需启用
- ⚙️ **环境变量支持**: 添加 `ENABLE_WS_RAW_LOG` 和 `ENABLE_LLM_RAW_LOG` 环境变量

## [2.2.0] - 2026-02-13

- ✨ **WebSocket run_command 响应回传**: Agent 执行命令后自动回传 commandResponse，提升工具调用体验
- 🔧 **断线时队列处理优化**: 断线时自动完成队列中的 run_command futures，避免请求卡死
- ⚡ **流式响应处理优化**: 优化增量事件内容缓存和处理逻辑，提升流式输出稳定性
- 🔄 **响应处理逻辑重构**: 重构流式与非流式响应处理流程，移除手动工具链回退逻辑
- 📝 **配置外部化**: Minecraft 命令配置和消息模板迁移至配置文件，便于定制
- 🔧 **CLI 入口统一**: 重构应用入口至 cli.py，统一命令行工具
- 🧪 **测试完善**: 完善基于 agent.iter() 的流式输出模式测试

## [2.1.0] - 2026-02-08

- ✨ 新增 MCWiki 搜索工具，支持查询 Minecraft Wiki
- 🔧 Agent 工具定义重构，独立 `tools.py` 模块
- ⚡ 启动时预热 LLM 模型，提高首次响应速度
- 📝 流式输出优化，按完整句子发送
- 📡 支持通过 ScriptEvent 方式发送聊天消息
- 🔊 优化日志输出与响应记录

## [2.0.0] - 2026-02-06

- 🎉 初始版本发布
- 🚀 现代化异步架构重构
- 🤖 PydanticAI Agent 框架集成
- 🔌 多 LLM 提供商支持
- 🎮 完整的游戏内命令系统
