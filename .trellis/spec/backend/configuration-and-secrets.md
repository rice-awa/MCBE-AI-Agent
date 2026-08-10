# 配置与敏感信息

## 配置来源

运行时配置分成两层：

- `config.json` 保存普通应用配置，模板是 [`config.example.json`](../../../config.example.json)。服务启动和 `python cli.py info` 依赖它。
- `.env` 保存密钥、密码等敏感值，模板是 [`.env.example`](../../../.env.example)。`config.json` 中通过 `${VAR}` 引用这些值；`.env` 和 `config.json` 都不提交。

[`config/settings.py`](../../../config/settings.py) 负责读取 JSON、加载 `.env`/进程环境变量、解析 `${VAR}`、校验必填路径并构造 Pydantic `Settings`。进程环境变量覆盖同名 dotenv 值，但普通配置不应因此迁移到新的 `.env` 字段。

## 新增配置字段

新增普通配置时按以下顺序完成：

1. 在 `config.example.json` 写入可运行的默认值和正确的嵌套路径。
2. 在 `config/settings.py` 的对应 Pydantic 模型中声明类型、默认值和验证规则。
3. 如果 Settings 会映射给 SDK，在 `services/gateway/settings_map.py` 中显式转换，保留 SDK 与宿主的边界。
4. 增加 `tests/test_json_settings.py` 或相邻测试，覆盖默认值、缺失字段、环境变量引用和非法值。
5. 在 `CLAUDE.md` / `README.md` 的配置表中补充用户可见字段；密钥只写变量名，不写真实值。

新增敏感字段只放 `.env.example` 和配置引用中，不要把 API key、JWT secret、WebSocket 密码、完整凭据或 token 写入 Python 默认值、日志、Trace 或审计记录。

## 兼容和迁移

- `addon.protocol.*` 是 deprecated/ignored 配置/文档镜像；运行时线协议固定为 MCBEWS/1，
  所有 wire/schema/framing 值来自 SDK `0.2.1` manifest，旧 `mcbeai` 值不得重新启用旧协议。
  兼容读取可以产生 deprecation 诊断，但不能覆盖 SDK profile。
- `flow_control.ai_resp*` 等旧键如仍需读取，应在设置迁移边界映射到 `text_resp*`，不要让调用点同时维护两套字段。
- 配置错误要在启动边界快速失败，并指出 JSON 路径和环境变量名；不要等到第一次聊天或第一次桥调用时才暴露缺失配置。

参考测试：[`tests/test_json_settings.py`](../../../tests/test_json_settings.py)、[`tests/test_cli_config_validation.py`](../../../tests/test_cli_config_validation.py)、[`tests/test_gateway_settings_map.py`](../../../tests/test_gateway_settings_map.py)。

## SDK 发布门禁

Host 的依赖约束为 `mcbe-ws-sdk>=0.2.1,<0.3.0`。发布顺序必须是先合并/发布 SDK `v0.2.1` 并
验证 PyPI wheel artifact，再合并 Host/Add-on；默认 root contract workflow 从构建 wheel 的
隔离环境运行，不得用 nested editable checkout 掩盖缺失 API。真实 MCBE sender/source、ToolPlayer
owner、Unicode/461 字节、长 session 与 approval 断线清理属于发布前手工 smoke，而非默认 pytest。
