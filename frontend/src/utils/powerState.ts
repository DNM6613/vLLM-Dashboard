import type { BmcStatus } from '../types';

// Power state decision for the header power button. The BMC is the
// authoritative source when it is actually reporting (powerFresh = this
// probe succeeded): IPMI reads the hardware power directly, so a machine
// that is truly off must flip the button to "Power ON" even while
// SSH/API reachability is still settling (incident 2026-10-21: after a
// power-off the shared SSH probe cache stayed connected, keeping the
// button stuck on "Power OFF" with no way to power the machine back on).
// A stale 'off' held while the BMC is unreachable must not hide a machine
// that is actually up and serving, so when the BMC is not fresh we fall
// back to raw reachability.
export function deriveServerOn(
  bmc: Pick<BmcStatus, 'power' | 'powerFresh'>,
  sshConnected: boolean,
  apiConnected: boolean,
): boolean {
  const bmcPowerAuthoritative = !!bmc.powerFresh && bmc.power !== null;
  return bmcPowerAuthoritative ? bmc.power === 'on' : (sshConnected || apiConnected);
}
