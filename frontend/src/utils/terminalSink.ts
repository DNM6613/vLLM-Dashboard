export type TerminalSink = {
  write: (data: string) => void;
  reset: () => void;
  /** Current terminal geometry (audit 2026-10-20 P2-14). */
  getSize?: () => { cols: number; rows: number };
};

let sink: TerminalSink | null = null;

export function registerTerminalSink(s: TerminalSink | null) {
  sink = s;
}

export function terminalWrite(data: string) {
  sink?.write(data);
}

export function terminalReset() {
  sink?.reset();
}

export function terminalSize(): { cols: number; rows: number } | null {
  return sink?.getSize?.() ?? null;
}
