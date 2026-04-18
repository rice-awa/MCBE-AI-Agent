import { world } from "@minecraft/server";
import { chunkBridgePayload } from "./chunking";
import { BRIDGE_MAX_CHUNK_CONTENT_LENGTH, TOOL_PLAYER_NAME, } from "./constants";
const TOOL_PLAYER_DIMENSION = "overworld";
const TOOL_PLAYER_LOCATION = { x: 300000, y: 100, z: 300000 };
const TOOL_PLAYER_CHECK_INTERVAL_TICKS = 20 * 30;
let isToolPlayerInitialized = false;
function loadServerModule() {
    return (0, eval)('import("@minecraft/server")');
}
function loadGameTestModule() {
    return (0, eval)('import("@minecraft/server-gametest")');
}
export function ensureToolPlayer() {
    void Promise.all([loadServerModule(), loadGameTestModule()]).then(([serverModule, gameTestModule]) => {
        const { GameMode, world } = serverModule;
        const existing = world
            .getAllPlayers()
            .find((player) => player.name === TOOL_PLAYER_NAME);
        if (existing) {
            return;
        }
        gameTestModule.spawnSimulatedPlayer(Object.assign({ dimension: world.getDimension(TOOL_PLAYER_DIMENSION) }, TOOL_PLAYER_LOCATION), TOOL_PLAYER_NAME, GameMode.Creative);
    });
}
export function sendBridgeResponseChunks(requestId, payload) {
    const toolPlayer = world
        .getAllPlayers()
        .find((player) => player.name === TOOL_PLAYER_NAME);
    if (!toolPlayer) {
        throw new Error("Tool player is not available");
    }
    const chunks = chunkBridgePayload(requestId, payload, BRIDGE_MAX_CHUNK_CONTENT_LENGTH);
    for (const chunk of chunks) {
        toolPlayer.runCommand(`tell @s ${chunk}`);
    }
}
export function initializeToolPlayer() {
    if (isToolPlayerInitialized) {
        return;
    }
    isToolPlayerInitialized = true;
    void loadServerModule().then(({ system }) => {
        ensureToolPlayer();
        system.runInterval(() => {
            ensureToolPlayer();
        }, TOOL_PLAYER_CHECK_INTERVAL_TICKS);
    });
}
//# sourceMappingURL=toolPlayer.js.map