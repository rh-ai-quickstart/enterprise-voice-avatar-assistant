import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["src/admin/test-setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // PatternFly imports its CSS from its components
    css: false,
  },
});
