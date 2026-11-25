import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import * as figmaPlugin from '@figma/eslint-plugin-figma-plugins';
import { defineConfig } from 'eslint/config';

export default defineConfig(
  { ignores: ['node_modules/', 'code.js', 'tools/'] },
  js.configs.recommended,
  tseslint.configs.recommended,
  {
    files: ['**/*.ts'],
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: { project: './tsconfig.json' },
    },
    plugins: { '@figma/figma-plugins': figmaPlugin },
    rules: {
      ...figmaPlugin.flatConfigs.recommended.rules,
      '@typescript-eslint/no-unused-vars': ['error', {
        argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_',
      }],
    },
  },
);
