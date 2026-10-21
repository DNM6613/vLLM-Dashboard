import { describe, expect, it } from 'vitest';
import { deriveServerOn } from './powerState';

// Incident 2026-10-21: after a BMC power-off the shared SSH probe cache
// stayed connected, so `sshConnected || apiConnected` kept the button
// stuck on "Power OFF" and the platform had no way to power the machine
// back on. The BMC (IPMI) power state is the authoritative source whenever
// it is actually reporting (powerFresh).
describe('deriveServerOn', () => {
  it('BMC fresh "off" flips the button to power-on even while SSH/API still report reachable (incident case)', () => {
    expect(deriveServerOn({ power: 'off', powerFresh: true }, true, true)).toBe(false);
    expect(deriveServerOn({ power: 'off', powerFresh: true }, true, false)).toBe(false);
    expect(deriveServerOn({ power: 'off', powerFresh: true }, false, false)).toBe(false);
  });

  it('BMC fresh "on" keeps the button on power-off even while SSH/API are down', () => {
    expect(deriveServerOn({ power: 'on', powerFresh: true }, false, false)).toBe(true);
    expect(deriveServerOn({ power: 'on', powerFresh: true }, true, false)).toBe(true);
  });

  it('stale "off" held while the BMC is unreachable falls back to reachability', () => {
    expect(deriveServerOn({ power: 'off', powerFresh: false }, true, false)).toBe(true);
    expect(deriveServerOn({ power: 'off', powerFresh: false }, false, false)).toBe(false);
  });

  it('a missing powerFresh flag (legacy payload) falls back to reachability', () => {
    expect(deriveServerOn({ power: 'off' }, true, false)).toBe(true);
    expect(deriveServerOn({ power: 'on' }, false, false)).toBe(false);
  });

  it('an unknown BMC power (null) falls back to reachability', () => {
    expect(deriveServerOn({ power: null, powerFresh: true }, true, false)).toBe(true);
    expect(deriveServerOn({ power: null, powerFresh: true }, false, true)).toBe(true);
    expect(deriveServerOn({ power: null, powerFresh: true }, false, false)).toBe(false);
  });
});
