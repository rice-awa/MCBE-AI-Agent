function createObservable(initialValue) {
    let value = initialValue;
    return {
        getData() {
            return value;
        },
        setData(nextValue) {
            value = nextValue;
        },
    };
}
export function createAgentUiState() {
    return {
        bridgeStatus: createObservable("disconnected"),
        lastPrompt: createObservable(""),
        lastResponsePreview: createObservable(""),
    };
}
//# sourceMappingURL=state.js.map