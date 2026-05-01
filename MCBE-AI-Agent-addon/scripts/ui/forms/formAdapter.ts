import { ActionFormData, ModalFormData } from "@minecraft/server-ui";
import type { ActionFormResponse, ModalFormResponse } from "@minecraft/server-ui";
import type { Player } from "@minecraft/server";

export function createActionForm(title: string, body: string): ActionFormData {
  return new ActionFormData().title(title).body(body);
}

export function createModalForm(title: string): ModalFormData {
  return new ModalFormData().title(title);
}

export async function showActionFormSafely(
  player: Player,
  form: ActionFormData,
): Promise<ActionFormResponse | undefined> {
  try {
    return await form.show(player);
  } catch {
    player.sendMessage("MCBE AI Agent: 面板暂时无法打开，请稍后再试。");
    return undefined;
  }
}

export async function showModalFormSafely(
  player: Player,
  form: ModalFormData,
): Promise<ModalFormResponse | undefined> {
  try {
    return await form.show(player);
  } catch {
    player.sendMessage("MCBE AI Agent: 表单暂时无法打开，请稍后再试。");
    return undefined;
  }
}
