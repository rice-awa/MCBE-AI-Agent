import { system, world } from "@minecraft/server";
import { registerBridgeRouter } from "./bridge/router";
import { initializeToolPlayer } from "./bridge/toolPlayer";
import { registerResponseSyncHandler } from "./bridge/responseSync";
import { registerUiEntry } from "./ui/entry";

/**
 * 早期执行阶段的安全初始化。
 * 仅注册事件订阅，不访问任何 world 状态（如玩家、维度、实体）。
 */
export function initializeAddonEarly(): void {
  registerBridgeRouter();
  registerResponseSyncHandler();
  registerUiEntry();
}

/**
 * 世界加载后的延迟初始化。
 * 双重策略：
 * 1. 订阅 worldLoad 事件 — 覆盖首次启动场景（世界尚未加载）
 * 2. system.run 兜底 — 覆盖 /reload 场景（世界已加载，worldLoad 不再触发）
 *
 * initializeToolPlayer 内部有幂等保护，不会重复初始化。
 */
export function initializeAddonAfterWorldLoad(): void {
  const init = (): void => {
    initializeToolPlayer();
  };

  // 首次启动：世界尚未加载，等待 worldLoad 事件
  world.afterEvents.worldLoad.subscribe(init);

  // /reload 兜底：世界已加载时，system.run 在下一个 tick 尝试初始化
  // 如果世界未就绪则抛出异常，init 幂等保护确保 worldLoad 回调仍能执行
  system.run(() => {
    try {
      init();
    } catch {
      // 世界尚未就绪，等待 worldLoad 事件触发
    }
  });
}
