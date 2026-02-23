'use strict';
/**
 * Unit tests for status command.
 * E-007-S-005: AC-E007-S005 (proxy status, masked key, exit codes)
 */

const fs = require('fs');
const os = require('os');
const path = require('path');

describe('collectStatus', () => {
  let tmpDir;
  let origEnv;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ongarde-status-test-'));
    origEnv = process.env.ONGARDE_HOME;
    process.env.ONGARDE_HOME = tmpDir;
    Object.keys(require.cache).filter(k => k.includes('openclaw/src')).forEach(k => delete require.cache[k]);
  });

  afterEach(() => {
    if (origEnv === undefined) delete process.env.ONGARDE_HOME;
    else process.env.ONGARDE_HOME = origEnv;
    fs.rmSync(tmpDir, { recursive: true, force: true });
    Object.keys(require.cache).filter(k => k.includes('openclaw/src')).forEach(k => delete require.cache[k]);
  });

  test('returns stopped when no PID file', async () => {
    const { collectStatus } = require('../../src/lib/status');
    const status = await collectStatus(59995); // unlikely port
    expect(status.proxyRunning).toBe(false);
    expect(status.stalePid).toBe(false);
  });

  test('returns stale-pid when PID file has dead PID', async () => {
    const { writePid } = require('../../src/lib/backup');
    writePid(999999999); // almost certainly not a real PID
    Object.keys(require.cache).filter(k => k.includes('openclaw/src/lib/status')).forEach(k => delete require.cache[k]);

    const { collectStatus } = require('../../src/lib/status');
    const status = await collectStatus(59994);
    expect(status.stalePid).toBe(true);
    expect(status.proxyRunning).toBe(false);
  });

  test('reads API key masked info from state.json', async () => {
    const { writeState } = require('../../src/lib/backup');
    writeState({
      api_key_id: '01HXQ7F9V8K5M3N2P0R4T6W8Y1',
      api_key_masked: '...8Y1',
      ongarde_port: 4242,
    });
    Object.keys(require.cache).filter(k => k.includes('openclaw/src/lib/status')).forEach(k => delete require.cache[k]);

    const { collectStatus } = require('../../src/lib/status');
    const status = await collectStatus(59993);
    expect(status.apiKeyMasked).toBe('...8Y1');
    expect(status.apiKeyId).toBe('01HXQ7F9V8K5M3N2P0R4T6W8Y1');
  });

  test('API key masked value never reveals plaintext', async () => {
    const { writeState } = require('../../src/lib/backup');
    writeState({ api_key_masked: '...8Y1' });
    Object.keys(require.cache).filter(k => k.includes('openclaw/src/lib/status')).forEach(k => delete require.cache[k]);

    const { collectStatus } = require('../../src/lib/status');
    const status = await collectStatus(59992);
    // Ensure no plaintext ong-... key
    const masked = status.apiKeyMasked;
    if (masked) {
      expect(masked).not.toMatch(/^ong-[A-Z0-9]{20,}/);
    }
  });
});

describe('getExitCode', () => {
  test('returns 0 when running and healthy', () => {
    const { getExitCode } = require('../../src/lib/status');
    const status = {
      proxyRunning: true,
      health: { scanner: 'healthy' },
    };
    expect(getExitCode(status)).toBe(0);
  });

  test('returns 1 when proxy stopped', () => {
    const { getExitCode } = require('../../src/lib/status');
    expect(getExitCode({ proxyRunning: false })).toBe(1);
  });

  test('returns 2 when running but scanner degraded', () => {
    const { getExitCode } = require('../../src/lib/status');
    const status = {
      proxyRunning: true,
      health: { scanner: 'degraded' },
    };
    expect(getExitCode(status)).toBe(2);
  });

  test('returns 2 when running but scanner error', () => {
    const { getExitCode } = require('../../src/lib/status');
    const status = {
      proxyRunning: true,
      health: { scanner: 'error' },
    };
    expect(getExitCode(status)).toBe(2);
  });
});

describe('runStatus --json output', () => {
  test('--json flag outputs valid JSON with required fields', async () => {
    const output = [];
    jest.spyOn(console, 'log').mockImplementation((...args) => output.push(args.join(' ')));

    const { runStatus } = require('../../src/commands/status');
    const result = await runStatus({ port: 59991, json: true });

    jest.restoreAllMocks();

    expect(output.length).toBeGreaterThan(0);
    const combined = output.join('\n');
    const parsed = JSON.parse(combined);

    // Validate required JSON fields (AC-E007-S005-08)
    expect(parsed).toHaveProperty('proxy');
    expect(parsed).toHaveProperty('port');
    expect(parsed).toHaveProperty('scanner');
    expect(parsed).toHaveProperty('dashboard');
    expect(parsed.dashboard).toMatch(/http:\/\/localhost:/);

    // API key must be masked or null — never plaintext
    if (parsed.api_key_masked !== null) {
      expect(parsed.api_key_masked).not.toMatch(/^ong-[A-Z0-9]{20,}/);
    }
  });
});
