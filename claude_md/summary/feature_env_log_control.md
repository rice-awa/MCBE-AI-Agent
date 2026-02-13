# 功能新增：环境变量控制日志打印

## 概述

新增了两个环境变量用于控制 WebSocket 和 LLM 原始日志的打印。

## 新增配置项

| 环境变量 | 默认值 | 说明 |
|---------|-------|------|
| `ENABLE_WS_RAW_LOG` | `True` | 是否启用 WebSocket 原始请求/响应日志 |
| `ENABLE_LLM_RAW_LOG` | `True` | 是否启用 LLM 请求/响应日志 |

## 修改的文件

### 1. `config/settings.py`

在日志配置区域添加了两个新的 `Field` 配置项：

```python
# 原始日志开关配置
enable_ws_raw_log: bool = Field(
    default=True,
    alias="ENABLE_WS_RAW_LOG",
    description="是否启用 WebSocket 原始请求/响应日志"
)
enable_llm_raw_log: bool = Field(
    default=True,
    alias="ENABLE_LLM_RAW_LOG",
    description="是否启用 LLM 请求/响应日志"
)
```

### 2. `config/logging.py`

修改 `setup_logging` 函数，新增三个参数：
- `enable_ws_raw_log`: 控制 WebSocket 原始日志
- `enable_llm_raw_log`: 控制 LLM 原始日志

实现逻辑：
- 当对应开关为 `False` 时，停止为该日志器添加文件处理器
- 同时将日志级别设置为 `CRITICAL` 以完全阻止日志输出

### 3. `cli.py`

在调用 `setup_logging` 时传递新增的配置参数。

## 使用方式

在 `.env` 文件中添加：

```bash
# 禁用 WebSocket 原始日志
ENABLE_WS_RAW_LOG=False

# 禁用 LLM 原始日志
ENABLE_LLM_RAW_LOG=False

# 同时禁用两者
ENABLE_WS_RAW_LOG=False
ENABLE_LLM_RAW_LOG=False
```

## 影响

- 设置为 `False` 时，对应的日志将不会输出到文件，也不会在控制台显示
- 影响的日志包括：
  - `websocket.raw` - WebSocket 接收的原始请求和发送的响应
  - `llm.raw` - LLM 的原始请求和响应
