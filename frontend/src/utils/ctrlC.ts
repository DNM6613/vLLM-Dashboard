export const CTRL_C_DOUBLE_MS = 1000;

export type CtrlCDecision = 'copy' | 'sigint';

export function decideCtrlC(
  now: number,
  lastCtrlCAt: number,
  windowMs: number = CTRL_C_DOUBLE_MS,
): CtrlCDecision {
  return now - lastCtrlCAt <= windowMs ? 'sigint' : 'copy';
}
