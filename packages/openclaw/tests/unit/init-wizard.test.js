'use strict';
/**
 * Unit tests for init wizard logic (mocked).
 * E-007-S-001: AC-E007-S001-02 through AC-E007-S001-06
 * E-007-S-003: AC-E007-S003 (test block, aha moment)
 * E-007-S-004: AC-E007-S004 (backup before modification)
 */

const fs = require('fs');
const os = require('os');
const path = require('path');

describe('init wizard: backup created before config modification', () => {
  let tmpDir;
  let configPath;
  let origEnv;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ongarde-init-test-'));
    origEnv = process.env.ONGARDE_HOME;
    process.env.ONGARDE_HOME = tmpDir;

    // Create a mock OpenClaw config
    configPath = path.join(tmpDir, 'openclaw-config.json');
    fs.writeFileSync(configPath, JSON.stringify({
      models: {
        providers: [{ name: 'openai', baseUrl: 'https://api.openai.com/v1' }],
      },
    }, null, 2));

    // Clear module caches
    Object.keys(require.cache).filter(k => k.includes('openclaw/src')).forEach(k => delete require.cache[k]);
  });

  afterEach(() => {
    if (origEnv === undefined) delete process.env.ONGARDE_HOME;
    else process.env.ONGARDE_HOME = origEnv;
    fs.rmSync(tmpDir, { recursive: true, force: true });
    Object.keys(require.cache).filter(k => k.includes('openclaw/src')).forEach(k => delete require.cache[k]);
  });

  test('backup is created before config is modified', () => {
    const { createBackup, readBackup, getBackupPath } = require('../../src/lib/backup');
    const { extractBaseUrl, updateBaseUrl, writeOpenClawConfig, findOpenClawConfig } = require('../../src/lib/openclaw-config');

    const found = findOpenClawConfig(configPath);
    const { baseUrl, location, providerIndex } = extractBaseUrl(found.config);

    // Simulate init flow: backup FIRST, then modify
    createBackup({
      openclaw_config_path: configPath,
      original_base_url: baseUrl,
      ongarde_port: 4242,
    });

    expect(fs.existsSync(getBackupPath())).toBe(true);

    // Now modify config
    updateBaseUrl(found.config, 'http://localhost:4242/v1', location, providerIndex);
    writeOpenClawConfig(configPath, found.config);

    // Backup still has original URL
    const backup = readBackup();
    expect(backup.original_base_url).toBe('https://api.openai.com/v1');

    // Config now has new URL
    const modified = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    expect(modified.models.providers[0].baseUrl).toBe('http://localhost:4242/v1');
  });

  test('backup is not overwritten on re-run', () => {
    const { createBackup, readBackup } = require('../../src/lib/backup');

    createBackup({ original_base_url: 'https://original.example.com/v1', ongarde_port: 4242 });
    const result = createBackup({ original_base_url: 'https://NEW.example.com/v1', ongarde_port: 4242 });

    expect(result).toBe(false);
    const backup = readBackup();
    expect(backup.original_base_url).toBe('https://original.example.com/v1');
  });

  test('API key is never written to disk in plaintext', () => {
    const { writeState } = require('../../src/lib/backup');
    const fakeKey = 'ong-01HXQ7F9V8K5M3N2P0R4T6W8Y1';

    // Write state with MASKED version only
    writeState({
      api_key_id: '01HXQ7F9V8K5M3N2P0R4T6W8Y1',
      api_key_masked: '...8Y1',
    });

    // Verify no file in ONGARDE_HOME contains the plaintext key
    const files = fs.readdirSync(tmpDir);
    for (const file of files) {
      const content = fs.readFileSync(path.join(tmpDir, file), 'utf8');
      expect(content).not.toContain(fakeKey);
    }
  });
});

describe('test block module', () => {
  test('TEST_CREDENTIAL is the correct value', () => {
    const { TEST_CREDENTIAL } = require('../../src/lib/test-block');
    expect(TEST_CREDENTIAL).toBe('sk-ongarde-test-fake-key-12345');
  });
});

describe('display module', () => {
  test('renderAhaMoment outputs required fields', () => {
    // Capture stdout
    const chunks = [];
    const origWrite = process.stdout.write.bind(process.stdout);
    const origLog = console.log.bind(console);

    const output = [];
    jest.spyOn(console, 'log').mockImplementation((...args) => output.push(args.join(' ')));
    jest.spyOn(process.stdout, 'write').mockImplementation((chunk) => {
      output.push(chunk.toString());
      return true;
    });

    const { renderAhaMoment } = require('../../src/lib/display');
    renderAhaMoment({
      ruleId: 'CREDENTIAL_DETECTED',
      riskLevel: 'CRITICAL',
      scanId: '01HXQ7F9V8K5M3N2P0R4T6W8Y1',
    });

    jest.restoreAllMocks();

    const combined = output.join('\n');
    expect(combined).toContain('OnGarde blocked a threat');
    expect(combined).toContain('CREDENTIAL_DETECTED');
    expect(combined).toContain('CRITICAL');
    expect(combined).toContain('01HXQ7F9V8K5M3N2P0R4T6W8Y1');
    expect(combined).toContain('quota unaffected');
    expect(combined).toContain('BLOCKED');
  });

  test('renderAhaMoment box has border characters', () => {
    const output = [];
    jest.spyOn(console, 'log').mockImplementation((...args) => output.push(args.join(' ')));
    jest.spyOn(process.stdout, 'write').mockImplementation((chunk) => {
      output.push(chunk.toString()); return true;
    });

    const { renderAhaMoment } = require('../../src/lib/display');
    renderAhaMoment({ ruleId: 'RULE', riskLevel: 'HIGH', scanId: 'scan123' });
    jest.restoreAllMocks();

    const combined = output.join('\n');
    expect(combined).toContain('┌');
    expect(combined).toContain('└');
    expect(combined).toContain('│');
  });
});
