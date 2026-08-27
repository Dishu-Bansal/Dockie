import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// base: "./" keeps the built site portable: it renders from any static host
// path (GitHub Pages subfolder, S3 bucket, nginx subdir, file:// preview).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "./",
});
