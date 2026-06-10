import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
});

const eslintConfig = [
  // Ignore generated/test infrastructure files that use CJS require patterns
  {
    ignores: [
      "tests/**",
      ".next/**",
      "node_modules/**",
    ],
  },
  ...compat.extends("next/core-web-vitals"),
];

export default eslintConfig;
