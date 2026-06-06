import { ActionFormData, ModalFormData } from "@minecraft/server-ui";
import type { ActionFormResponse, ModalFormResponse } from "@minecraft/server-ui";
import type { Player } from "@minecraft/server";
import * as ServerUiModule from "@minecraft/server-ui";

export type DduiObservable<T> = {
  getData(): T;
  setData(value: T): void;
};

type DduiDropdownOption<T> = {
  label: string;
  value: T;
  description?: string;
};

export type DduiCustomForm = {
  divider(): DduiCustomForm;
  header(text: string | DduiObservable<string>): DduiCustomForm;
  label(text: string | DduiObservable<string>): DduiCustomForm;
  textField(
    label: string,
    value: DduiObservable<string>,
    options?: { description?: string },
  ): DduiCustomForm;
  spacer(): DduiCustomForm;
  closeButton(): DduiCustomForm;
  close(): void;
  toggle(
    label: string,
    value: DduiObservable<boolean>,
    options?: { description?: string },
  ): DduiCustomForm;
  slider(
    label: string,
    value: DduiObservable<number>,
    min: number,
    max: number,
    options?: { description?: string; step?: number },
  ): DduiCustomForm;
  dropdown(
    label: string,
    value: DduiObservable<number>,
    options: DduiDropdownOption<number>[],
  ): DduiCustomForm;
  button(
    label: string,
    callback: () => void,
    options?: { tooltip?: string },
  ): DduiCustomForm;
  show(): Promise<unknown>;
};

export type DduiCustomFormShowResult = {
  ok: boolean;
  closedByUser: boolean;
  closeReason?: unknown;
};

type DduiServerUiModule = {
  CustomForm: {
    create(player: Player, title: string): DduiCustomForm;
  };
  Observable: {
    create<T>(initialValue: T, options: { clientWritable: true }): DduiObservable<T>;
  };
};

const dduiServerUi = ServerUiModule as unknown as Partial<DduiServerUiModule>;

export function createActionForm(title: string, body: string): ActionFormData {
  return new ActionFormData().title(title).body(body);
}

export function createModalForm(title: string): ModalFormData {
  return new ModalFormData().title(title);
}

export function createDduiObservable<T>(initialValue: T): DduiObservable<T> {
  const observableFactory = dduiServerUi.Observable;
  if (!observableFactory) {
    throw new Error("DDUI Observable API is unavailable.");
  }
  return observableFactory.create<T>(initialValue, { clientWritable: true });
}

export function createCustomForm(player: Player, title: string): DduiCustomForm {
  const customFormFactory = dduiServerUi.CustomForm;
  if (!customFormFactory) {
    throw new Error("DDUI CustomForm API is unavailable.");
  }
  return customFormFactory.create(player, title);
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
      ok: closeReason !== false && !isUserBusyReason(closeReason),
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
