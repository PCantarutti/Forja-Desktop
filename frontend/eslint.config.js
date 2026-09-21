// Lint da interface. O que ele existe para pegar é uma classe de bug só: hook com dependência
// faltando ou sem limpeza — listener que fica registrado, stream que não é abortado, closure velha.
// Foi exatamente isso que apareceu na varredura, e nenhum desses erra o tipo, então o tsc não vê.
import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // As regras novas do compilador do React (setState dentro de efeito, ref lida no render)
      // apontam padrões reais, mas endereçá-las é refatoração de componente, não conserto: ficam
      // como aviso, visíveis, sem travar a build de quem só quis mudar um texto.
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/refs": "warn",
      // `any` é usado de propósito em vários lugares (payload do SSE, meta das mensagens): avisar
      // sem quebrar a build é honesto, transformar em erro seria uma refatoração à força.
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      "no-empty": ["error", { allowEmptyCatch: true }],
    },
  },
);
