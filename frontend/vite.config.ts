import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Pure client-side SPA. There is no co-located backend: the app talks directly
// to the assembly-grasp Lambda Function URL, which streams a filtered TSV back
// to the browser for immediate download. See AWS_SETUP.md.
export default defineConfig({
  plugins: [react()],
  server: { host: "0.0.0.0", port: 5173 },
  preview: { host: "0.0.0.0", port: 4173 },
  build: { outDir: "dist", sourcemap: false },
});
