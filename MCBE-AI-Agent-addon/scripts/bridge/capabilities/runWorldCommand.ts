import { world } from "@minecraft/server";

const COMMAND_DENYLIST = ["stop", "reload", "kick", "op", "deop"];

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

  const keyword = command.split(/\s+/, 1)[0]?.toLowerCase() ?? "";
  if (COMMAND_DENYLIST.includes(keyword)) {
    return {
      ok: false,
      payload: { output: `命令 ${keyword} 不允许通过 addon 执行`, successCount: 0 },
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
