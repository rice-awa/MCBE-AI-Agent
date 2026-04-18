import type { Player } from "@minecraft/server";

import { showAgentConsole } from "./panels/agentConsole";
import { createAgentUiState } from "./state";

const uiState = createAgentUiState();

export function registerUiEntry(): void {
  // 第一阶段只保留入口和状态容器，不抢占聊天命令。
}

export async function openAgentUi(player: Player): Promise<void> {
  await showAgentConsole(player, uiState);
}
