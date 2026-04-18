export type BridgeStatus = "disconnected" | "connecting" | "ready";

export type ObservableLike<T> = {
  getData(): T;
  setData(value: T): void;
};

export type AgentUiState = {
  bridgeStatus: ObservableLike<BridgeStatus>;
  lastPrompt: ObservableLike<string>;
  lastResponsePreview: ObservableLike<string>;
};

function createObservable<T>(initialValue: T): ObservableLike<T> {
  let value = initialValue;
  return {
    getData(): T {
      return value;
    },
    setData(nextValue: T): void {
      value = nextValue;
    },
  };
}

export function createAgentUiState(): AgentUiState {
  return {
    bridgeStatus: createObservable<BridgeStatus>("disconnected"),
    lastPrompt: createObservable(""),
    lastResponsePreview: createObservable(""),
  };
}
