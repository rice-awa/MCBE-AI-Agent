import { world } from "@minecraft/server";
const COMMAND_DENYLIST = ["stop", "reload", "kick", "op", "deop"];
export function handleRunWorldCommand(payload) {
    var _a, _b, _c, _d;
    const command = (_b = (_a = payload.command) === null || _a === void 0 ? void 0 : _a.trim()) !== null && _b !== void 0 ? _b : "";
    if (!command) {
        return {
            ok: false,
            payload: { output: "命令不能为空", successCount: 0 },
        };
    }
    const keyword = (_d = (_c = command.split(/\s+/, 1)[0]) === null || _c === void 0 ? void 0 : _c.toLowerCase()) !== null && _d !== void 0 ? _d : "";
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