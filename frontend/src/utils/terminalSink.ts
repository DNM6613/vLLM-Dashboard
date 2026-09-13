export type TerminalSink = {
  write: (data: string) => void;
  reset: () => void;
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
