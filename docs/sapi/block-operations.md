# Minecraft Script API — 方块操作参考

> 模块：`@minecraft/server`  
> 文档视图：Experimental（Microsoft Learn）  
> 整理用途：MCBE AI Agent / Addon Bridge 接入方块读写能力  
> 来源：Context7 拉取的官方 Script API 文档 + JaylyDev 镜像交叉对照

**总入口**

- [Script API Reference (experimental)](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/?view=minecraft-bedrock-experimental)
- 镜像（签名对照，更新较快）：[jaylydev/scriptapi-docs](https://jaylydev.github.io/scriptapi-docs/latest/)

---

## 1. 能力边界

| 能做 | 不能做 |
|------|--------|
| 改方块类型、block states（`BlockPermutation`） | 任意 Block Entity **原始 NBT** |
| 含水、区域填充 | Java 式 `/data merge block` 自由 compound |
| 容器 / 告示牌 / 唱片机等 **typed component** | 未暴露的 BE 字段（命令方块代码、刷怪笼完整 NBT 等） |
| 动态属性（脚本自定义 KV） | 跨 content pack 读写 dynamic properties |

**一句话**：主路径是 **permutation（类型 + states）+ 少量 typed block components**；**没有**通用 `setBlockNbt`。

---

## 2. 接入约定

```ts
import {
  world,
  BlockPermutation,
  ItemStack,
  BlockVolume,
  // 按需：
  // BlockInventoryComponent, BlockSignComponent, ...
} from "@minecraft/server";
```

### 2.1 通用约束（多数写操作）

- 不能在 **restricted-execution mode** 调用
- 不能在 **early-execution mode** 调用（需 world ready）
- 可能抛：
  - `LocationInUnloadedChunkError`
  - `LocationOutOfWorldBoundariesError`
  - `UnloadedChunksError`（区域操作）
- 写前检查：`block && block.isValid`

### 2.2 安全包装建议

```ts
function safeEdit(block: Block | undefined, fn: (b: Block) => void): boolean {
  if (!block || !block.isValid) return false;
  try {
    fn(block);
    return true;
  } catch (e) {
    // LocationInUnloadedChunkError | LocationOutOfWorldBoundariesError
    // UnloadedChunksError | ContainerRulesError | InvalidBlockComponentError
    console.error(e);
    return false;
  }
}
```

---

## 3. 获取方块

### 3.1 按坐标

```ts
const dim = world.getDimension("overworld");
const block = dim.getBlock({ x: 0, y: 64, z: 0 }); // Block | undefined
```

- chunk 未加载时通常为 `undefined`（或后续访问抛错）
- 原文：[Dimension](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/dimension)

### 3.2 邻接 / 偏移

```ts
block.above();                 // y+1
block.below();                 // y-1
block.north(steps?);           // z-
block.south(steps?);
block.east(steps?);            // x+
block.west(steps?);
block.offset({ x, y, z });
```

- 原文：[Block](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/block)

### 3.3 射线

```ts
const hit = dim.getBlockFromRay(location, direction, options?);
// BlockRaycastHit | undefined
```

- 原文：[Dimension.getBlockFromRay](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/dimension)

---

## 4. 读取方块

### 4.1 `Block` 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `dimension` | `Dimension` | 所在维度 |
| `location` / `x` `y` `z` | `Vector3` / `number` | 坐标 |
| `type` | `BlockType` | 类型对象 |
| `typeId` | `string` | 如 `minecraft:stone`（官方建议比较用 `matches`） |
| `permutation` | `BlockPermutation` | 含 states 的完整配置 |
| `isAir` | `boolean` | 是否空气 |
| `isLiquid` | `boolean` | 是否液体（含水方块 **不算** liquid） |
| `isWaterlogged` | `boolean` | 是否含水 |
| `isSolid` | `boolean` | **pre-release**，签名可能变 |
| `isValid` | `boolean` | 引用是否仍有效（卸载后 false） |
| `localizationKey` | `string` | 本地化 key |

原文：[Block 属性](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/block)

### 4.2 Tag / 红石 / 匹配

```ts
block.hasTag("stone");           // boolean
block.getRedstonePower();        // number | undefined
// block.matches(typeId, states?) — 官方推荐用于比较
```

原文：[Block](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/block)

### 4.3 `BlockPermutation` 读 states

```ts
const perm = block.permutation;

perm.type;                                           // BlockType
perm.getState("minecraft:cardinal_direction");       // value | undefined
perm.getAllStates();                                 // Record<string, boolean | number | string>
perm.getTags();                                      // string[]
perm.getItemStack(amount?);                          // ItemStack | undefined
```

| 方法 | 签名 | 说明 |
|------|------|------|
| `getState` | `getState(stateName): T \| undefined` | 读单个 state |
| `getAllStates` | `getAllStates(): Record<...>` | 全部 states |
| `getTags` | `getTags(): string[]` | permutation tags |
| `getItemStack` | `getItemStack(amount?): ItemStack \| undefined` | 原型物品栈（amount 1–255） |

原文：[BlockPermutation](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/blockpermutation)

---

## 5. 写入方块（类型 + States）— 主路径

### 5.1 构建 `BlockPermutation`

```ts
// A. 类型 + 初始 states
const perm = BlockPermutation.resolve("minecraft:oak_stairs", {
  "minecraft:cardinal_direction": "north",
  "minecraft:vertical_half": "bottom",
});

// B. 在已有 permutation 上改 state（immutable，返回新对象）
const next = block.permutation.withState(
  "minecraft:cardinal_direction",
  "east"
);
```

| API | 签名 | 说明 |
|-----|------|------|
| `BlockPermutation.resolve` | `resolve(blockName, states?)` | 由 id + 可选 states 解析 |
| `withState` | `withState(stateName, value)` | 派生新 permutation，原对象不变 |

原文：[BlockPermutation](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/blockpermutation)

### 5.2 应用到世界 — `Block`

```ts
block.setPermutation(perm);           // 整块换成指定 permutation
block.setType("minecraft:chest");     // 只换类型（默认 permutation）
block.trySetPermutation(perm);        // boolean；pre-release，先校验再设
block.setWaterlogged(true);           // 含水
```

| 方法 | 签名 | 备注 |
|------|------|------|
| `setPermutation` | `(permutation: BlockPermutation): void` | **主写接口** |
| `setType` | `(blockType: BlockType \| string): void` | 使用默认 states |
| `trySetPermutation` | `(permutation: BlockPermutation): boolean` | **pre-release**，可能变更/移除 |
| `setWaterlogged` | `(isWaterlogged: boolean): void` | 楼梯等可含水块 |

原文：[Block](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/block)

### 5.3 应用到世界 — `Dimension`

```ts
dim.setBlockPermutation(location, perm);
dim.setBlockType(location, "minecraft:stone"); // 默认 permutation
```

| 方法 | 签名 | 说明 |
|------|------|------|
| `setBlockPermutation` | `(location: Vector3, permutation: BlockPermutation): void` | 按坐标设 permutation |
| `setBlockType` | `(location: Vector3, blockType: BlockType \| string): void` | 按坐标设类型 |

原文：[Dimension](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/dimension)

### 5.4 最小可接入封装

```ts
import { world, BlockPermutation, Vector3 } from "@minecraft/server";

export function setBlock(
  dimId: string,
  location: Vector3,
  typeId: string,
  states?: Record<string, string | number | boolean>
): boolean {
  const dim = world.getDimension(dimId);
  const block = dim.getBlock(location);
  if (!block?.isValid) return false;

  const perm = BlockPermutation.resolve(typeId, states as any);
  block.setPermutation(perm);
  return true;
}

export function setBlockState(
  dimId: string,
  location: Vector3,
  stateName: string,
  value: string | number | boolean
): boolean {
  const block = world.getDimension(dimId).getBlock(location);
  if (!block?.isValid) return false;

  const next = block.permutation.withState(stateName as any, value as any);
  block.setPermutation(next);
  return true;
}
```

---

## 6. 区域填充

```ts
import { BlockVolume, BlockPermutation } from "@minecraft/server";

const volume = new BlockVolume(
  { x: 0, y: 64, z: 0 },
  { x: 10, y: 70, z: 10 }
);

const placed = dim.fillBlocks(
  volume,
  BlockPermutation.resolve("minecraft:stone"), // 或 BlockType | string
  options? // BlockFillOptions：include / exclude filter 等
);
// 返回 ListBlockVolume（实际被放置的块）
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `volume` | `BlockVolumeBase` | 填充范围 |
| `block` | `BlockPermutation \| BlockType \| string` | 填充物 |
| `options` | `BlockFillOptions?` | 过滤 include / exclude |

- 可能抛 `UnloadedChunksError`（体积跨未加载区）
- 原文：[Dimension.fillBlocks](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/dimension)

---

## 7. 方块组件（结构化 Block Entity 数据）

### 7.1 获取组件

```ts
const comp = block.getComponent("inventory");
// 或 "minecraft:inventory" / BlockComponentTypes.Inventory
// 无组件 → undefined
```

- 原文：[Block.getComponent](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/block)
- 映射：[BlockComponentTypeMap](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/blockcomponenttypemap)
- 枚举：[BlockComponentTypes](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/blockcomponenttypes)

### 7.2 已文档化组件一览

| Component ID | 类 | 主要用途 |
|--------------|-----|----------|
| `inventory` | `BlockInventoryComponent` | 箱子等容器 |
| `sign` | `BlockSignComponent` | 告示牌文字 / 染料 / 蜡 |
| `record_player` | `BlockRecordPlayerComponent` | 唱片机 |
| `fluid_container` | `BlockFluidContainerComponent` | 炼药锅等流体 |
| `dynamic_properties` | `BlockDynamicPropertiesComponent` | 脚本自定义 KV |
| `piston` | `BlockPistonComponent` | 活塞 |
| `movable` | `BlockMovableComponent` | 可移动性 |
| `map_color` | `BlockMapColorComponent` | 地图颜色 |
| `redstone_producer` | `BlockRedstoneProducerComponent` | 红石产出 |
| `instrument_sound` | `BlockInstrumentComponent` | 乐器 / 音符声 |
| `precipitation_interactions` | `BlockPrecipitationInteractionsComponent` | 降水交互 |

### 7.3 容器 `inventory` + `Container`

```ts
block.setType("minecraft:chest");
const inv = block.getComponent("inventory"); // BlockInventoryComponent
const c = inv?.container;
if (!c) return;

c.size;                 // number，单箱通常 27
c.emptySlotsCount;
c.isValid;

c.setItem(0, new ItemStack("minecraft:apple", 10)); // undefined 清空
c.getItem(0);           // ItemStack | undefined
c.addItem(itemStack);   // 自动找空位 / 堆叠
c.clearAll();
c.transferItem(fromSlot, otherContainer);
c.swapItems(slot, otherSlot, otherContainer);
```

| API | 说明 |
|-----|------|
| `setItem(slot, itemStack?)` | 指定槽；`undefined` 清空 |
| `getItem(slot)` | 读槽，空为 `undefined` |
| `addItem(itemStack)` | 自动堆叠 / 空位 |
| `clearAll()` | 清空 |
| `transferItem` / `swapItems` | 容器间转移 / 交换 |

原文：

- [BlockInventoryComponent](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/blockinventorycomponent)
- [Container](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/container)

### 7.4 告示牌 `sign`

```ts
const sign = block.getComponent("sign");
sign?.setText("Hello");                    // string | RawMessage，最长 512
sign?.getText(side?);
sign?.getRawText(side?);
sign?.setTextDyeColor(color?, side?);
sign?.getTextDyeColor(side?);
sign?.setWaxed(true);
sign?.isWaxed;
```

| 方法 | 说明 |
|------|------|
| `setText(message, side?)` | 设文字；>512 字符抛错 |
| `getText` / `getRawText` | 读 string / RawText |
| `setTextDyeColor` / `getTextDyeColor` | 染料 |
| `setWaxed` / `isWaxed` | 上蜡防编辑 |

原文：[BlockSignComponent](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/blocksigncomponent)

### 7.5 唱片机 `record_player`

```ts
const rp = block.getComponent("record_player");
rp?.setRecord("minecraft:music_disc_cat", true); // type, startPlaying?
rp?.playRecord();
rp?.ejectRecord();
rp?.getRecord();   // ItemStack | undefined
rp?.isPlaying();   // boolean
```

| 方法 | 说明 |
|------|------|
| `setRecord(recordItemType?, startPlaying?)` | 放入并可选立即播放 |
| `playRecord()` | 播放当前唱片 |
| `ejectRecord()` | 弹出 |
| `getRecord()` | 当前唱片 |
| `isPlaying()` | 是否在播 |

原文：[BlockRecordPlayerComponent](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/blockrecordplayercomponent)

### 7.6 流体容器 `fluid_container`

```ts
const fluid = block.getComponent("fluid_container");
fluid?.getFluidType();
fluid?.setFluidType(fluidType);
fluid?.setPotion(itemStack);
fluid?.addDye(dyeItemType);
```

原文：[BlockFluidContainerComponent](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/blockfluidcontainercomponent)

### 7.7 动态属性 `dynamic_properties`

```ts
const dp = block.getComponent("dynamic_properties");
dp?.set("my:key", 123);              // boolean | number | string | Vector3
dp?.get("my:key");                   // 同上 | undefined
dp?.set("my:key", undefined);        // 删除
dp?.totalByteCount();                // 当前字节；每 pack 约 1KB 上限
```

**注意**

- key **按 content pack 隔离**，不能读其他 pack 写入的值
- 这是脚本自定义数据，**不是**原版 NBT 字段

原文：[BlockDynamicPropertiesComponent](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/blockdynamicpropertiescomponent)

---

## 8. 结构（Structure）内改块

```ts
// Structure 实例上：
structure.setBlockPermutation(localLocation, blockPermutation?, waterlogged?);
structure.getIsWaterlogged(localLocation);
// 再通过 structureManager.place(...) 放回世界
```

原文：[Structure](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/structure)

---

## 9. 事件

| 事件 / 类 | 用途 |
|-----------|------|
| `BlockComponentBlockStateChangeEvent` | 某块 permutation 变更，含 `previousPermutation` |

原文：[BlockComponentBlockStateChangeEvent](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/blockcomponentblockstatechangeevent)

订阅入口视引擎版本为 `world.afterEvents` / block custom component 事件系统，接入时以目标版本文档为准。

---

## 10. 错误对照

| 错误 | 常见场景 |
|------|----------|
| `LocationInUnloadedChunkError` | chunk 未加载 |
| `LocationOutOfWorldBoundariesError` | 坐标出界 |
| `UnloadedChunksError` | `fillBlocks` 跨未加载区 |
| `ContainerRulesError` | 容器规则冲突（如某些物品限制） |
| `InvalidContainerError` | 容器已失效 |
| `InvalidBlockComponentError` | 组件无效 / 方块已变 |

---

## 11. 接入决策树

```text
要改什么？
├─ 方块 id / 方向 / 开关 / 水位 / 台阶半块 …
│   └─ BlockPermutation.resolve + setPermutation / withState
├─ 含水
│   └─ setWaterlogged
├─ 一片区域
│   └─ fillBlocks
├─ 箱子内容
│   └─ getComponent("inventory").container.*
├─ 告示牌字
│   └─ getComponent("sign").setText*
├─ 唱片机
│   └─ getComponent("record_player")
├─ 炼药锅流体
│   └─ getComponent("fluid_container")
├─ 自己脚本存数据
│   └─ getComponent("dynamic_properties")
└─ 任意 NBT / 命令方块 / 刷怪笼完整数据
    └─ Script API 无通用接口 → 命令 / 结构预置 / 行为包侧
```

---

## 12. 推荐业务封装形状

业务层不要直接散落 `setPermutation`，建议统一薄封装：

```ts
// block-ops.ts — 薄封装，业务只依赖这层
export interface BlockOps {
  get(dim: string, pos: Vector3): BlockSnapshot | null;
  setType(dim: string, pos: Vector3, typeId: string): boolean;
  setStates(
    dim: string,
    pos: Vector3,
    states: Record<string, string | number | boolean>
  ): boolean;
  setPermutation(
    dim: string,
    pos: Vector3,
    typeId: string,
    states?: Record<string, string | number | boolean>
  ): boolean;
  setWaterlogged(dim: string, pos: Vector3, value: boolean): boolean;
  fill(
    dim: string,
    from: Vector3,
    to: Vector3,
    typeId: string,
    states?: object
  ): number;
  inventory(dim: string, pos: Vector3): Container | null;
  signSetText(dim: string, pos: Vector3, text: string, side?: number): boolean;
  dpGet(dim: string, pos: Vector3, key: string): unknown;
  dpSet(
    dim: string,
    pos: Vector3,
    key: string,
    value?: boolean | number | string | Vector3
  ): boolean;
}
```

实现即 §5–§7 组合；**不要**假设存在 `setNbt`。

若经 Addon Bridge 暴露给 Python 宿主，capability 命名建议对齐：

| capability（建议） | 对应 Script 能力 |
|--------------------|------------------|
| `block_get` | `getBlock` + 读 typeId / states / tags |
| `block_set` | `setPermutation` / `setType` |
| `block_set_state` | `withState` + `setPermutation` |
| `block_fill` | `fillBlocks` |
| `block_inventory_*` | `inventory` + `Container` |
| `block_sign_set_text` | `sign.setText` |
| `block_dp_get` / `block_dp_set` | `dynamic_properties` |

（线协议仍走 `mcbews:bridge_req`，见 `docs/addon-bridge-protocol.md`。）

---

## 13. 原文链接索引

| 主题 | 链接 |
|------|------|
| Script API 总览（experimental） | https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/?view=minecraft-bedrock-experimental |
| `Block` | https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/block |
| `BlockPermutation` | https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/blockpermutation |
| `Dimension` | https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/dimension |
| `BlockInventoryComponent` | https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/blockinventorycomponent |
| `Container` | https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/container |
| `BlockSignComponent` | https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/blocksigncomponent |
| `BlockRecordPlayerComponent` | https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/blockrecordplayercomponent |
| `BlockFluidContainerComponent` | https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/blockfluidcontainercomponent |
| `BlockDynamicPropertiesComponent` | https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/blockdynamicpropertiescomponent |
| `BlockComponentTypeMap` | https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/blockcomponenttypemap |
| `BlockComponentTypes` | https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/blockcomponenttypes |
| `Structure` | https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/structure |
| `BlockComponentBlockStateChangeEvent` | https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/blockcomponentblockstatechangeevent |
| JaylyDev 镜像 | https://jaylydev.github.io/scriptapi-docs/latest/ |

---

## 14. 版本与实验性说明

- Microsoft Learn 整站常挂在 `minecraft-bedrock-experimental` 视图下；许多 API 已进入稳定 `@minecraft/server` 模块，但具体能力是否要求 Beta APIs / 世界实验开关，取决于：
  - 游戏引擎版本
  - `manifest.json` 中 `@minecraft/server` 的 module version
  - `min_engine_version`
- 明确标 **pre-release** 的 API（如 `trySetPermutation`、`isSolid`）接入时需可降级，勿作硬依赖。
- 接入前用目标版本的 JaylyDev `latest` / `preview` 再对一次签名。

---

*文档生成自官方 Script API 文档检索；若引擎升级导致签名变更，以 Microsoft Learn / JaylyDev 原文为准。*
