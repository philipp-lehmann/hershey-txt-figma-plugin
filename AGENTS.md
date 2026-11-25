# AGENTS.md

Figma plugin that converts a text layer into single-stroke vector paths (Hershey-style fonts) for pen plotters and CNC.

## Layout

| Path | What it is |
| --- | --- |
| `code.ts` | Plugin logic (runs in Figma's sandbox). The bottom part between `// fonts:start` and `// fonts:end` is **generated**: never edit it by hand. |
| `code.js` | Build output of `code.ts` (gitignored). Figma loads this. |
| `ui.html` | Plugin UI (iframe). Plain HTML/JS, no build step. |
| `manifest.json` | Figma manifest (`documentAccess: dynamic-page`, no network access). |
| `fonts/*.svg` | Single-stroke SVG fonts: the source of truth for glyph data. |
| `tools/fonts.json` | List of fonts shown in the plugin, in dropdown order. |
| `tools/build_fonts.py` | Converts outline fonts and regenerates the font data in `code.ts`. |
| `tools/outline_to_stroke.py` | TTF/OTF → single-stroke SVG font (skeletonisation). |

## Commands

```sh
npm install
npm run build        # tsc → code.js
npm run lint         # ESLint 9 flat config + Figma plugin rules; must pass
npm run fonts        # python3 tools/build_fonts.py
```
Python tools need `fonttools numpy scipy scikit-image pillow`.

## Adding a font

1. Add an entry to `tools/fonts.json`:
   - existing single-stroke SVG font in `fonts/`: `{ "label": "...", "svg": "File.svg" }`
   - outline font: add a `convert` block with `url` (or local `file`, relative to `tools/`), optional `axes` (e.g. `{ "wght": 300 }`, since lighter weights skeletonise more cleanly), and `source` / `designer` / `link` / `license` metadata.
2. `npm run fonts` (only converts fonts whose SVG is missing; `--only "Label"` or `--force` to redo).
3. `npm run build`, then check the glyphs visually: crossings (`$`, `€`) and angled terminals are where conversion artefacts show up.
4. Only use fonts whose licence allows modification and redistribution (e.g. SIL OFL), and credit them under *Sources* in `README.md`.

## Font data conventions

- All fonts use units-per-em 1000 and ascent 800, y-up from the baseline (`UNITS_PER_EM` / `ASCENT` in `code.ts`).
- SVG glyph paths use only `M` and `L`; the generated data is a point list where `null` means pen up.
- Glyphs without strokes (space) are kept for their advance width.

## Figma plugin conventions

- Keep `npm run lint` clean: it flags deprecated or sync APIs that break with `dynamic-page` (use the `*Async` variants, e.g. `getNodeByIdAsync`).
- UI colours use Figma theme variables (`var(--figma-color-*)`, enabled by `themeColors: true`). Don't hardcode colours or rely on `prefers-color-scheme`.
- Messages between UI and plugin are typed in `code.ts` (`UiMessage`, `PluginMessage`); update both sides together.
- Persist user preferences with `figma.clientStorage`, not `localStorage` (unavailable in the plugin iframe).
- `FONTS` is defined at the end of `code.ts`, so startup code lives in `main()`, which is called on the last line.

## Commit messages

One line, `type(scope): description` (types: feat, fix, style, refactor, perf, docs, chore), imperative, lower case, max 72 chars.
