import { world } from "@minecraft/server";
import { findDeniedCommand } from "./commandSafety";
export function handleRunWorldCommand(payload) {
    var _a, _b;
    const command = (_b = (_a = payload.command) === null || _a === void 0 ? void 0 : _a.trim()) !== null && _b !== void 0 ? _b : "";
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
    }
    catch (error) {
        return {
            ok: false,
            payload: {
                output: error instanceof Error ? error.message : String(error),
                successCount: 0,
            },
        };
    }
}
//# sourceMappingURL=runWorldCommand.js.map