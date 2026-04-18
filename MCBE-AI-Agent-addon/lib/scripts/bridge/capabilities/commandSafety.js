const COMMAND_DENYLIST = ["stop", "reload", "kick", "op", "deop"];
export function extractCommandEntrypoints(command) {
    const entrypoints = [];
    const segments = command
        .toLowerCase()
        .split(/[;\n\r]+/)
        .map((segment) => segment.trim())
        .filter(Boolean);
    for (const segment of segments) {
        const tokens = segment.split(/\s+/).filter(Boolean);
        if (tokens.length === 0) {
            continue;
        }
        entrypoints.push(tokens[0]);
        for (let index = 0; index < tokens.length - 1; index += 1) {
            if (tokens[index] === "run") {
                entrypoints.push(tokens[index + 1]);
            }
        }
    }
    return entrypoints;
}
export function findDeniedCommand(command) {
    const entrypoints = extractCommandEntrypoints(command);
    return entrypoints.find((entrypoint) => COMMAND_DENYLIST.includes(entrypoint));
}
//# sourceMappingURL=commandSafety.js.map