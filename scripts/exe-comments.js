// afterSign do electron-builder: grava "Comments" nos detalhes do Forja.exe.
// O electron-builder não tem opção para esse campo; usa o mesmo rcedit que ele já chama.
const path = require("path");
const { executeAppBuilder } = require("builder-util");

exports.default = async (ctx) => {
  if (ctx.electronPlatformName !== "win32") return;
  const exe = path.join(ctx.appOutDir, `${ctx.packager.appInfo.productFilename}.exe`);
  const args = [exe, "--set-version-string", "Comments", "All-in-one AI Harness: chat, agente, geração de imagem e vídeo local."];
  await executeAppBuilder(["rcedit", "--args", JSON.stringify(args)]);
};
