import { ActionFormData, ModalFormData } from "@minecraft/server-ui";
export function showAgentConsole(player, uiState) {
    return __awaiter(this, void 0, void 0, function* () {
        var _a, _b;
        const entryForm = new ActionFormData()
            .title("MCBE AI Agent")
            .body(`桥接状态: ${uiState.bridgeStatus.getData()}\n最近问题: ${uiState.lastPrompt.getData() || "无"}\n最近响应: ${uiState.lastResponsePreview.getData() || "无"}`)
            .button("输入问题")
            .button("关闭");
        const response = yield entryForm.show(player);
        if (response.canceled || response.selection !== 0) {
            return;
        }
        const promptForm = new ModalFormData()
            .title("MCBE AI Agent")
            .textField("提问", "后续将通过桥接转发到 Python", {
            defaultValue: uiState.lastPrompt.getData(),
        })
            .submitButton("保存预览");
        const promptResponse = yield promptForm.show(player);
        if (promptResponse.canceled) {
            return;
        }
        const nextPrompt = String((_b = (_a = promptResponse.formValues) === null || _a === void 0 ? void 0 : _a[0]) !== null && _b !== void 0 ? _b : "");
        uiState.lastPrompt.setData(nextPrompt);
        uiState.lastResponsePreview.setData("已记录，后续将接入桥接发送");
        uiState.bridgeStatus.setData(nextPrompt ? "connecting" : "disconnected");
    });
}
//# sourceMappingURL=agentConsole.js.map