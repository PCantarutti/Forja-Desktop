/**
 * Roda o app sem empacotar: Electron + o backend do venv de backend/.venv, servindo ui/dist.
 *
 * Para mexer na interface com hot reload, deixe `npx vite` rodando em ui/ (ele faz proxy de /api)
 * em vez de usar este script.
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dist = path.join(ROOT, "ui", "dist");

if (!fs.existsSync(dist)) {
  console.error("ui/dist não existe. Rode antes:  cd ui && npm install && npm run build");
  process.exit(1);
}

const electron = path.join(ROOT, "node_modules", ".bin", process.platform === "win32" ? "electron.cmd" : "electron");
spawn(electron, ["."], { cwd: ROOT, stdio: "inherit", shell: process.platform === "win32", env: { ...process.env, FORJA_WEB: dist } })
  .on("exit", (code) => process.exit(code ?? 0));
