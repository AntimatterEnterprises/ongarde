'use strict';
/**
 * Unit tests for src/lib/port-check.js
 * E-007-S-001: AC-E007-S001-06 port conflict detection
 */

const net = require('net');
const { isPortInUse, validatePort } = require('../../src/lib/port-check');

describe('isPortInUse', () => {
  test('returns false for port with nothing listening', async () => {
    // Port 1 is always unavailable/refused on standard systems (requires root)
    // Use a high port that is very unlikely to be in use
    const result = await isPortInUse(59999);
    expect(result).toBe(false);
  });

  test('returns true for a port that is listening', async () => {
    const server = net.createServer();
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    const port = server.address().port;

    try {
      const result = await isPortInUse(port);
      expect(result).toBe(true);
    } finally {
      await new Promise((resolve) => server.close(resolve));
    }
  });

  test('returns false quickly on ECONNREFUSED', async () => {
    const start = Date.now();
    const result = await isPortInUse(59998);
    const elapsed = Date.now() - start;
    expect(result).toBe(false);
    expect(elapsed).toBeLessThan(3000); // Should be fast
  });
});

describe('validatePort', () => {
  test('accepts valid port 4242', () => {
    expect(validatePort('4242')).toBe(4242);
    expect(validatePort(4242)).toBe(4242);
  });

  test('accepts boundary values', () => {
    expect(validatePort(1024)).toBe(1024);
    expect(validatePort(65535)).toBe(65535);
  });

  test('rejects port below 1024', () => {
    expect(() => validatePort(80)).toThrow();
    expect(() => validatePort(1023)).toThrow();
  });

  test('rejects port above 65535', () => {
    expect(() => validatePort(65536)).toThrow();
    expect(() => validatePort(99999)).toThrow();
  });

  test('rejects non-numeric input', () => {
    expect(() => validatePort('abc')).toThrow();
    expect(() => validatePort('')).toThrow();
    expect(() => validatePort(null)).toThrow();
  });
});
