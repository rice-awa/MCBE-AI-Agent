# MCP 超时问题修复总结

## 问题描述

运行时报错：
```
TimeoutError: Cancelled via cancel scope; reason: deadline exceeded
```

错误堆栈显示问题发生在 MCP 工具集初始化时：
- `services/agent/core.py:426` - `active_agent.iter()` 调用
- MCP 工具集在 `__aenter__` 时超时

## 根本原因

在 `config/settings.py` 的 `merge_mcp_config` 方法中：

```python
if self.mcp_servers_json:
    # ... 解析配置 ...
    self.mcp.enabled = True  # 这里会覆盖 MCP_ENABLED=false 的设置！
```

当配置了 `MCP_SERVERS` 环境变量（即使值为空或示例配置）时，代码会强制设置 `self.mcp.enabled = True`，这会覆盖 `MCP_ENABLED=false` 的设置。

这导致：
1. PydanticAI 尝试连接到配置的 MCP 服务器
2. 连接超时（默认 10 秒）
3. 抛出 `TimeoutError` 异常

## 修复方案

修改 `config/settings.py` 中的 `merge_mcp_config` 方法，在解析 `MCP_SERVERS` 之前先检查 `mcp_enabled` 是否为 `True`：

```python
@model_validator(mode="after")
def merge_mcp_config(self) -> "Settings":
    """合并 MCP 配置 - 使用官方 mcpServers 字典格式"""
    # 只有在 MCP 启用时才解析和加载 MCP 服务器配置
    if not self.mcp_enabled:
        # 即使配置了 MCP_SERVERS，如果没有明确启用也不加载
        return self
    # ... 其余代码 ...
```

## 验证结果

修复后配置加载正确：
```
MCP enabled: False
MCP servers: {}
```

## 修改文件

- `config/settings.py:335-365` - `merge_mcp_config` 方法
