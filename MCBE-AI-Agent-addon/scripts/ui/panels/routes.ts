export type AgentPanelRoute =
  | { panel: "main" }
  | { panel: "more" }
  | { panel: "settings" }
  | { panel: "stats" }
  | { panel: "sessionFiles" }
  | { panel: "conversationList" }
  | { panel: "conversationPreview" }
  | { panel: "close" };

export const MAIN_ROUTE: AgentPanelRoute = { panel: "main" };
export const CLOSE_ROUTE: AgentPanelRoute = { panel: "close" };
