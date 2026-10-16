/**
 * xterm.js terminal color tokens (single source of truth for terminal theming).
 *
 * NOTE ON TOKEN POLICY: hex values are allowed at this token-definition layer
 * only. xterm renders to canvas and needs concrete colors at runtime — CSS
 * variables (rgb(var(--c-*))) cannot be resolved by the xterm theme API, so
 * these palettes are kept as static terminal protocol colors here instead of
 * in components.
 *
 * Color group semantics (both themes share the same structure):
 *  - background / foreground ... terminal canvas base layer
 *  - cursor / cursorAccent .... caret fill + text color under caret
 *  - selectionBackground ...... text selection highlight
 *  - black..white ............. ANSI 8 base palette
 *  - brightBlack..brightWhite . ANSI 8 bright palette
 *  Dark theme: GitHub-dark style palette matched to --c-console surface.
 *  Light theme: warm-paper palette matched to the light --c-console surface.
 */
import type { ITheme } from '@xterm/xterm';

export const XTERM_THEME_DARK: ITheme = {
  // base layer
  background: '#141c28',
  foreground: '#e6edf3',
  // caret
  cursor: '#e6edf3',
  cursorAccent: '#141c28',
  // selection
  selectionBackground: '#264f78',
  // ANSI 16 palette
  black: '#484f58',
  red: '#ff7b72',
  green: '#3fb950',
  yellow: '#d29922',
  blue: '#58a6ff',
  magenta: '#bc8cff',
  cyan: '#39c5cf',
  white: '#b1bac4',
  brightBlack: '#6e7681',
  brightRed: '#ffa198',
  brightGreen: '#56d364',
  brightYellow: '#e3b341',
  brightBlue: '#79c0ff',
  brightMagenta: '#d2a8ff',
  brightCyan: '#56d4dd',
  brightWhite: '#f0f6fc',
};

export const XTERM_THEME_LIGHT: ITheme = {
  // base layer
  background: '#faf6ec',
  foreground: '#3f3a2c',
  // caret
  cursor: '#3f3a2c',
  cursorAccent: '#faf6ec',
  // selection
  selectionBackground: '#e6dcbe',
  // ANSI 16 palette
  black: '#57534a',
  red: '#cf222e',
  green: '#1a7f37',
  yellow: '#9a6700',
  blue: '#0550ae',
  magenta: '#8250df',
  cyan: '#1b7c83',
  white: '#57534a',
  brightBlack: '#6e6a5c',
  brightRed: '#cf222e',
  brightGreen: '#1a7f37',
  brightYellow: '#9a6700',
  brightBlue: '#0550ae',
  brightMagenta: '#8250df',
  brightCyan: '#1b7c83',
  brightWhite: '#3f3a2c',
};
