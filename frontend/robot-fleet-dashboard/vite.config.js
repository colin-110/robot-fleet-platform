import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://localhost:8000",
        ws: true,
      },
    },
  },
  build: {
    sourcemap: true,

  },
  // JSX inside *.test.jsx is transformed by esbuild, which defaults to the
  // classic runtime and would need React in scope. The app's own files go
  // through plugin-react and are unaffected either way.
  esbuild: { jsx: "automatic" },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.js"],
    // Pin the build-time vars the app reads. Without this the suite picks up
    // whatever is in the developer's .env, so assertions on the handshake URL
    // pass on one machine and fail on another.
    env: {
      VITE_API_BASE_URL: "",
      VITE_WS_API_KEY: "test-api-key",
    },
    // Only our own specs. Without this, vitest walks node_modules looking for
    // anything matching the default include glob.
    include: ["src/**/*.{test,spec}.{js,jsx}"],
    coverage: {
      provider: "v8",
      reporter: ["text", "json-summary"],
      include: ["src/**/*.{js,jsx}"],
      exclude: ["src/main.jsx", "src/test/**", "src/**/*.{test,spec}.{js,jsx}"],
    },
  },
});
