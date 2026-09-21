/**
 * Roda o app sem empacotar: Electron + o backend do venv de backend/.venv, servindo frontend/dist.
 *
 * Para mexer na interface com hot reload, deixe `npx vite` rodando em frontend/ (ele faz proxy de /api)
 * em vez de usar este script.
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dist = path.join(ROOT, "frontend", "dist");

if (!fs.existsSync(dist)) {
  console.error("frontend/dist não existe. Rode antes:  cd frontend && npm install && npm run build");
  process.exit(1);
}

// O pacote `electron`, importado no Node, exporta o caminho do executável. É melhor do que o
// .cmd do node_modules/.bin: aquele exigia `shell: true`, e aí um caminho com espaço
// (C:\Program Files\...) quebrava, porque o Node não cita o comando ao passar pelo shell.
const { default: electron } = await import("electron");

const filho = spawn(electron, ["."], {
  cwd: ROOT,
  stdio: "inherit",
  env: { ...process.env, FORJA_WEB: dist },
});
filho.on("exit", (code) => process.exit(code ?? 0));
// Ctrl+C aqui tem que descer para o Electron, que é quem mata a árvore do backend.
for (const sinal of ["SIGINT", "SIGTERM"]) process.on(sinal, () => filho.kill(sinal));
