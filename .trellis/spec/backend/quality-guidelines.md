# 质量与测试

## 基本工具链

- Python 3.11+，4 空格缩进；模块/函数 `snake_case`，类 `PascalCase`，常量 `UPPER_CASE`。
- [`pyproject.toml`](../../../pyproject.toml) 是 Ruff、mypy 和 pytest 的配置源：行宽 100，Ruff 启用 `E/F/W/I/UP/B/C4/SIM`，mypy 使用 strict，pytest 默认排除 `live` marker。
- 依赖和运行命令以 [`requirements.txt`](../../../requirements.txt)、`README.md` 和 `CLAUDE.md` 为准，不在单个任务中引入未配置的新格式化/检查工具。

## 测试要求

新增功能或修复 bug 必须补充相邻测试；测试优先验证公开边界和失败路径，而不是依赖私有实现细节。现有测试模式包括：

- 异步服务使用 `pytest.mark.asyncio`，队列、Worker、provider 生命周期和 Gateway 生命周期通过 fake、mock、`asyncio.Event` 或临时资源测试。
- 配置和 JSONL 持久化使用 `tmp_path`，并验证缺失、非法、脱敏、轮转和恢复行为。
- 外部 provider、Minecraft 世界和真实网络不进入默认测试；需要真实连接的测试使用 `live` marker，因此 `pytest` 默认不会运行它们。
- 修改 block tools、工具审批、Trace 或 bridge 时，同时覆盖成功、参数无效、超时/取消、并发竞态和外部状态未知等分支。

参考：[`tests/test_agent_worker.py`](../../../tests/test_agent_worker.py)、[`tests/test_queue_context.py`](../../../tests/test_queue_context.py)、[`tests/test_block_ops.py`](../../../tests/test_block_ops.py)、[`tests/test_agent_trace.py`](../../../tests/test_agent_trace.py)。

## 必须遵守的项目约束

- 玩家相关状态必须显式携带 `player_name` / `sender`，并使用会话 Store 的分桶 API。
- 长文本下行必须经过 `BrokerResponseBridge` 或 SDK delivery；不要复制 `commandLine`、tellraw、scriptevent 或 `text_resp` 分片。
- Python 服务保持异步边界：Hook 不等待 LLM；队列负责解耦；生命周期停止要清理后台任务和连接资源。
- Pydantic 消息模型、工具结果和 bridge result 是跨层契约；不要让消费者各自定义同一 JSON 字段的私有解释。
- SDK 依赖契约必须在构建 wheel 后的隔离环境验证；默认 Host 测试不要求从 PyPI 安装尚未发布的
  `0.2.1`，本地应使用 SDK wheel。禁止让 nested editable checkout 掩盖 public/codec/Host API 漂移。
- `command_line_byte_budget=461` 是 empirical 兼容预算；Unicode 分片测试应覆盖 CJK、emoji 和
  wrapper 完整 UTF-8 字节数，而不是把 461 写成官方 API 上限。
- 不提交 `.env`、`config.json`、`data/`、`logs/`、密钥、缓存、编译产物或真实服务输出。

## 提交前检查

```bash
pytest
```

如果环境安装了仓库配置中声明的工具，再运行 `ruff check .` 和 `mypy .`；否则至少运行受影响的 pytest，并在交付说明中记录未运行的检查。涉及 Addon 时，还要进入 `MCBE-AI-Agent-addon/` 运行 `pnpm test`，必要时运行 `pnpm lint` 和 `pnpm build`。
