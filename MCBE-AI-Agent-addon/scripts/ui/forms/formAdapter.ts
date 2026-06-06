import { ActionFormData, CustomForm, ModalFormData, Observable } from "@minecraft/server-ui";
import type { ActionFormResponse, ModalFormResponse } from "@minecraft/server-ui";
import type { Player } from "@minecraft/server";

export type DduiObservable<T extends string | number | boolean> = Observable<T>;
export type DduiCustomForm = CustomForm;

export type DduiCustomFormShowResult = {
  ok: boolean;
  closedByUser: boolean;
  closeReason?: unknown;
};

export function createActionForm(title: string, body: string): ActionFormData {
  return new ActionFormData().title(title).body(body);
}

export function createModalForm(title: string): ModalFormData {
  return new ModalFormData().title(title);
}

export function createDduiObservable<T extends string | number | boolean>(initialValue: T): DduiObservable<T> {
  return Observable.create<T>(initialValue, { clientWritable: true });
}

export function createCustomForm(player: Player, title: string): DduiCustomForm {
  return CustomForm.create(player, title);
}

export function createDduiTextObservable(initialValue: string): DduiObservable<string> {
  return createDduiObservable<string>(initialValue);
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

export async function showCustomFormSafely(
  player: Player,
  form: DduiCustomForm,
): Promise<DduiCustomFormShowResult> {
  try {
    const closeReason = await form.show();
    return {
      ok: !isUserBusyReason(closeReason),
      closedByUser: isUserClosedReason(closeReason),
      closeReason,
    };
  } catch {
    player.sendMessage("MCBE AI Agent: 表单暂时无法打开，请稍后再试。");
    return { ok: false, closedByUser: false };
  }
}

function isUserClosedReason(reason: unknown): boolean {
  if (typeof reason === "string") {
    return reason === "UserClose" || reason === "UserClosed";
  }

  if (typeof reason === "boolean") {
    return !reason;
  }

  if (!reason || typeof reason !== "object") {
    return false;
  }

  const maybeReason = reason as { canceled?: unknown; closeReason?: unknown; cancelationReason?: unknown };
  if (maybeReason.canceled === true) {
    return true;
  }

  return (
    isUserClosedReason(maybeReason.closeReason)
    || isUserClosedReason(maybeReason.cancelationReason)
  );
}

function isUserBusyReason(reason: unknown): boolean {
  if (typeof reason === "string") {
    return reason === "UserBusy";
  }

  if (!reason || typeof reason !== "object") {
    return false;
  }

  const maybeReason = reason as { closeReason?: unknown; cancelationReason?: unknown };
  return (
    isUserBusyReason(maybeReason.closeReason)
    || isUserBusyReason(maybeReason.cancelationReason)
  );
}
