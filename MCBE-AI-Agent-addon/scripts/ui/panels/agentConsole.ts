import { ActionFormData, ModalFormData } from "@minecraft/server-ui";
import type { Player } from "@minecraft/server";

import type { AgentUiState } from "../state";

export async function showAgentConsole(player: Player, uiState: AgentUiState): Promise<void> {
  const entryForm = new ActionFormData()
    .title("MCBE AI Agent")
    .body(
      `桥接状态: ${uiState.bridgeStatus.getData()}\n最近问题: ${uiState.lastPrompt.getData() || "无"}\n最近响应: ${
        uiState.lastResponsePreview.getData() || "无"
      }`,
    )
    .button("输入问题")
    .button("关闭");

  const response = await entryForm.show(player);
  if (response.canceled || response.selection !== 0) {
    return;
  }

  const promptForm = new ModalFormData()
    .title("MCBE AI Agent")
    .textField("提问", "后续将通过桥接转发到 Python", {
      defaultValue: uiState.lastPrompt.getData(),
    })
    .submitButton("保存预览");

  const promptResponse = await promptForm.show(player);
  if (promptResponse.canceled) {
    return;
  }

  const nextPrompt = String(promptResponse.formValues?.[0] ?? "");
  uiState.lastPrompt.setData(nextPrompt);
  uiState.lastResponsePreview.setData("已记录，后续将接入桥接发送");
  uiState.bridgeStatus.setData(nextPrompt ? "connecting" : "disconnected");
}
