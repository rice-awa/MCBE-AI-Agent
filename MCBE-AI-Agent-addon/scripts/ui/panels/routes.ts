export type AgentPanelRoute =
  | { panel: "main" }
  | { panel: "chatInput" }
  | { panel: "history"; pageIndex?: number }
  | { panel: "settings" }
  | { panel: "stats" }
  | { panel: "close" };

export const MAIN_ROUTE: AgentPanelRoute = { panel: "main" };
export const CLOSE_ROUTE: AgentPanelRoute = { panel: "close" };
