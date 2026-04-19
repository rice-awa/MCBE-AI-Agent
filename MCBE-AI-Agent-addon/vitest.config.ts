import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@minecraft/server": new URL("./tests/__mocks__/minecraft-server.ts", import.meta.url)
        .pathname,
      "@minecraft/server-gametest": new URL(
        "./tests/__mocks__/minecraft-server-gametest.ts",
        import.meta.url,
      ).pathname,
      "@minecraft/server-ui": new URL("./tests/__mocks__/minecraft-server-ui.ts", import.meta.url)
        .pathname,
    },
  },
});
