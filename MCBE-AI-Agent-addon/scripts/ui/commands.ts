const UI_TRIGGER_ITEM = "minecraft:command_block";

export function isUiTriggerItem(typeId: string | undefined): boolean {
  return typeId === UI_TRIGGER_ITEM;
}

export function buildAgentChatCommand(input: string): string {
  return `AGENT 聊天 ${input.trim()}`;
}
