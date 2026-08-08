# 拆分 block_ops/tools_impl 校验函数到独立模块

## Goal

评估 `services/agent/block_ops/tools_impl.py` 中参数校验 helper 的规模与独立性，若值得拆分则新建 `validation.py` 模块。

## 背景

方案三从 `tools_impl.py` 拆出了 `limits.py`/`message.py`/`preflight.py`，文件体积从 62KB 降到约 31KB。但剩余的校验函数（`_normalize_position_array`、`_normalize_block_input`、`_validate_inspect_args`、`_validate_edit_args` 等）仍有足够独立性和复用价值。若 `tools_impl` 继续增长，校验函数应是独立的关注点。

## 范围

- `services/agent/block_ops/tools_impl.py` 中所有 `_normalize_*`、`_validate_*` 函数
- `services/agent/block_ops/__init__.py`（出口调整）
- 调用这些函数的 `preflight.py` 和 `tools_impl.py` 自身

## Acceptance Criteria

- [ ] 盘点 `tools_impl.py` 中所有校验/归一化 helper 并列出
- [ ] 决定拆分或保留（给出理由）
- [ ] 若拆分：`validation.py` 模块创建，函数迁移，导入更新
- [ ] `__init__.py` 按需调整
- [ ] `pytest -q tests/test_block_ops.py` 通过
- [ ] `pytest -q` 无新增失败