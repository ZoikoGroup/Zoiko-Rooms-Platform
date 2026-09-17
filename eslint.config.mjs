import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      // Flags legitimate mount-time syncs (reading localStorage, route-based
      // resets, debounced loading timers) that are standard in this codebase.
      "react-hooks/set-state-in-effect": "off",
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // A second `next dev` instance run from this same checkout (e.g. for
    // manual multi-account testing) uses NEXT_DIST_DIR to avoid the
    // single-instance-per-directory lock -- that build output needs the
    // same exclusion as the default .next/ above.
    ".next-*/**",
  ]),
]);

export default eslintConfig;
