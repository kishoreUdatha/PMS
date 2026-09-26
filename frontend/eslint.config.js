import js from '@eslint/js'
import globals from 'globals'
import tseslint from 'typescript-eslint'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import jsxA11y from 'eslint-plugin-jsx-a11y'

/** Rules that are right in spirit but fire across a large existing codebase
 *  for reasons that are style or long-standing patterns rather than bugs.
 *  They are warnings: visible, and worth fixing when a file is touched, but
 *  they do not fail `npm run lint`. Anything not listed keeps the severity its
 *  preset gives it. */
const isOff = (v) => v === 'off' || v === 0 || (Array.isArray(v) && isOff(v[0]))
const asWarnings = (rules) =>
  Object.fromEntries(Object.entries(rules)
    .filter(([, v]) => !isOff(v))
    .map(([r]) => [r, 'warn']))

export default tseslint.config(
  { ignores: ['dist', 'node_modules', 'tsconfig.tsbuildinfo'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
      'jsx-a11y': jsxA11y,
    },
    rules: {
      // Accessibility: the recommended set, reported as warnings so the
      // backlog is visible without blocking every change behind it.
      ...asWarnings(jsxA11y.flatConfigs.recommended.rules),

      // React Compiler-era checks from eslint-plugin-react-hooks 7. Useful
      // guidance, but this codebase predates them (setState in effects for
      // derived form state is everywhere), so they warn.
      ...asWarnings(reactHooks.configs['recommended-latest'].rules),
      // The two classic hooks rules. Breaking rules-of-hooks is a real bug
      // (hooks called in a different order between renders), so it fails
      // the lint. Missing dependencies are often deliberate here and are
      // reviewed case by case, so they warn.
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',

      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],

      // Unused things are errors except for deliberate `_`-prefixed ones.
      '@typescript-eslint/no-unused-vars': ['error', {
        argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrors: 'none',
      }],
      '@typescript-eslint/no-explicit-any': 'warn',
      // `cond && doThing()` and `void promise` read fine; flag as warnings.
      '@typescript-eslint/no-unused-expressions': ['warn', {
        allowShortCircuit: true, allowTernary: true,
      }],
    },
  },
)
