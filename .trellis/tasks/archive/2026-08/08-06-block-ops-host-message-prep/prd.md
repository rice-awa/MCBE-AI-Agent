# 方案三：集中 block_ops 宿主侧报文准备

## 目标

把宿主侧的目标归一化、限制规则、字节预算、Addon 报文、预检计划和结果裁剪集中到一个深模块，消除 `target.py` 对 `tools_impl` 的反向导入，并收窄 `block_ops/__init__.py` 的出口。

## 范围

- `services/agent/block_ops/tools_impl.py`
- `services/agent/block_ops/target.py:275-298`（函数内反向导入 `tools_impl.apply_limits_to_payload`）
- `services/agent/block_ops/__init__.py:3-89`（大量重导出）
- `services/agent/block_ops/bridge.py`、`preflight_cache.py`

## 现状问题

`tools_impl.py` 同时包含目标归一化后的处理、载荷构建、限制注入、命令行字节预算、预检、计划执行和结果处理。`target.py` 为给载荷应用限制，在函数内部反向导入 `tools_impl.apply_limits_to_payload`。函数内导入避免了导入期报错，但没消除职责反向依赖，只是把循环关系藏了起来。`block_ops/__init__.py` 重导出大量名称，使包接口偏大，调用方易依赖内部细节。

## 验收标准

- [ ] `target.py` 不再反向导入 `tools_impl`。
- [ ] 相对坐标、绝对坐标、单点和区域目标归一化集中处理。
- [ ] 数量限制与命令行字节预算集中处理。
- [ ] 预检、冻结计划和审批恢复集中处理。
- [ ] Addon 返回成功、部分失败、未知状态和超限有统一处理。
- [ ] `inspect_block`、`place_block`、`fill_block` 三个工具对同类错误使用一致的结果格式。
- [ ] `block_ops/__init__.py` 出口收窄（减少重导出内部细节）。

## 必须保留的约束

- 不改变 `inspect_block`、`place_block`、`fill_block` 的对外参数。
- 不恢复已移除的批量 `edit_blocks`。
- 不改变 mcbews v1 报文。
- `bridge.py` 继续作为 Addon 适配位置，不把协议调用细节塞回工具实现。

## 删除检验（存在价值依据）

若删除集中规则，查看、单格放置和区域填充会各自重新实现载荷、限制、预算和预检语义，因此这组规则具有很高复用收益。

## 验收门 / 回滚门（报告 12.1）

- **验收**：`target.py` 不再反向导入 `tools_impl`；`block_ops/__init__.py` 出口收窄。
- **回滚**：仍存在跨模块反向依赖，或三个工具对同类错误结果不一致。