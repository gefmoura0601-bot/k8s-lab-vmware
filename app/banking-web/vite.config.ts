import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/banking/",
  plugins: [react()],
  test: { environment: "node" }
});
