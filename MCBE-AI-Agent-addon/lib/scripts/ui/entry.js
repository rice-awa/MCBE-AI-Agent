import { showAgentConsole } from "./panels/agentConsole";
import { createAgentUiState } from "./state";
const uiState = createAgentUiState();
export function registerUiEntry() {
    // 第一阶段只保留入口和状态容器，不抢占聊天命令。
}
export function openAgentUi(player) {
    return __awaiter(this, void 0, void 0, function* () {
        yield showAgentConsole(player, uiState);
    });
}
//# sourceMappingURL=entry.js.map