import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const root = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  resolve: {
    alias: {
      "@minecraft/server": path.resolve(root, "tests/__mocks__/minecraft-server.ts"),
      "@minecraft/server-gametest": path.resolve(
        root,
        "tests/__mocks__/minecraft-server-gametest.ts",
      ),
      "@minecraft/server-ui": path.resolve(root, "tests/__mocks__/minecraft-server-ui.ts"),
    },
  },
});
