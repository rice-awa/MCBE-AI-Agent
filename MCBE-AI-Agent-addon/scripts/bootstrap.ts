import { system, world } from "@minecraft/server";
import { registerBridgeRouter } from "./bridge/router";
import { initializeToolPlayer } from "./bridge/toolPlayer";
import { registerResponseSyncHandler } from "./bridge/responseSync";
import { registerUiEntry } from "./ui/entry";

const DEBUG = true;

function log(message: string): void {
  if (DEBUG) {
    console.log(`[MCBE-AI] ${message}`);
  }
}

/**
 * 早期执行阶段的安全初始化。
 * 仅注册事件订阅，不访问任何 world 状态（如玩家、维度、实体）。
 */
export function initializeAddonEarly(): void {
  log("initializeAddonEarly: 开始早期初始化...");
  registerBridgeRouter();
  registerResponseSyncHandler();
  registerUiEntry();
  log("initializeAddonEarly: 早期初始化完成");
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
  log("initializeAddonAfterWorldLoad: 设置延迟初始化...");

  const init = (): void => {
    log("init: 尝试初始化 ToolPlayer...");
    try {
      initializeToolPlayer();
      log("init: ToolPlayer 初始化成功");
    } catch (error) {
      log(`init: ToolPlayer 初始化失败: ${error instanceof Error ? error.message : String(error)}`);
      throw error;
    }
  };

  // 首次启动：世界尚未加载，等待 worldLoad 事件
  log("initializeAddonAfterWorldLoad: 订阅 worldLoad 事件...");
  world.afterEvents.worldLoad.subscribe(() => {
    log("worldLoad 事件触发，执行初始化...");
    init();
  });

  // /reload 兜底：世界已加载时，system.run 在下一个 tick 尝试初始化
  // 如果世界未就绪则抛出异常，init 幂等保护确保 worldLoad 回调仍能执行
  log("initializeAddonAfterWorldLoad: 设置 system.run 兜底...");
  system.run(() => {
    try {
      log("system.run 兜底: 尝试立即初始化...");
      init();
    } catch (error) {
      log(`system.run 兜底: 初始化失败，等待 worldLoad 事件: ${error instanceof Error ? error.message : String(error)}`);
    }
  });
}
