import { world } from "@minecraft/server";

import { findDeniedCommand } from "./commandSafety";

export function handleRunWorldCommand(payload: {
  command?: string;
}): { ok: boolean; payload: { output: string; successCount: number } } {
  const command = payload.command?.trim() ?? "";
  if (!command) {
    return {
      ok: false,
      payload: { output: "命令不能为空", successCount: 0 },
    };
  }

  const deniedCommand = findDeniedCommand(command);
  if (deniedCommand) {
    return {
      ok: false,
      payload: { output: `命令 ${deniedCommand} 不允许通过 addon 执行`, successCount: 0 },
    };
  }

  try {
    const result = world.getDimension("overworld").runCommand(command);
    return {
      ok: true,
      payload: {
        output: "命令执行成功",
        successCount: result.successCount,
      },
    };
  } catch (error) {
    return {
      ok: false,
      payload: {
        output: error instanceof Error ? error.message : String(error),
        successCount: 0,
      },
    };
  }
}
