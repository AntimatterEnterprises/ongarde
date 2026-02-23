'use strict';
/**
 * Unit tests for rollback command.
 * E-007-S-004: AC-E007-S004-03 through AC-E007-S004-07
 */

const fs = require('fs');
const os = require('os');
const path = require('path');

describe('runRollback', () => {
  let tmpDir;
  let origEnv;
  let configPath;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ongarde-rollback-test-'));
    origEnv = process.env.ONGARDE_HOME;
    process.env.ONGARDE_HOME = tmpDir;

    // Create a mock OpenClaw config that has been updated to OnGarde proxy
    configPath = path.join(tmpDir, 'openclaw-config.json');
    fs.writeFileSync(configPath, JSON.stringify({
      models: {
        providers: [{ name: 'openai', baseUrl: 'http://localhost:4242/v1', apiKey: 'sk-test' }],
      },
      agents: ['coder'],
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

  test('rollback with no backup returns error (AC-E007-S004-03)', async () => {
    const { runRollback } = require('../../src/commands/rollback');
    const result = await runRollback();
    expect(result.success).toBe(false);
    expect(result.error).toMatch(/No backup/i);
  });

  test('rollback restores original baseUrl from backup', async () => {
    const { createBackup } = require('../../src/lib/backup');

    // Create backup with original URL
    createBackup({
      openclaw_config_path: configPath,
      original_base_url: 'https://api.openai.com/v1',
      ongarde_port: 4242,
    });

    Object.keys(require.cache).filter(k => k.includes('openclaw/src')).forEach(k => delete require.cache[k]);

    const { runRollback } = require('../../src/commands/rollback');
    const result = await runRollback();

    expect(result.success).toBe(true);

    // Verify config is restored
    const restored = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    expect(restored.models.providers[0].baseUrl).toBe('https://api.openai.com/v1');

    // Verify other fields are preserved
    expect(restored.models.providers[0].apiKey).toBe('sk-test');
    expect(restored.agents).toEqual(['coder']);
  });

  test('rollback verifies restored value matches original (AC-E007-S004-04)', async () => {
    const { createBackup } = require('../../src/lib/backup');
    createBackup({
      openclaw_config_path: configPath,
      original_base_url: 'https://api.openai.com/v1',
      ongarde_port: 4242,
    });

    Object.keys(require.cache).filter(k => k.includes('openclaw/src')).forEach(k => delete require.cache[k]);

    const { runRollback } = require('../../src/commands/rollback');
    const result = await runRollback();

    // Verification should have passed
    expect(result.success).toBe(true);
  });

  test('rollback does not delete backup file after completion', async () => {
    const { createBackup, getBackupPath } = require('../../src/lib/backup');
    createBackup({
      openclaw_config_path: configPath,
      original_base_url: 'https://api.openai.com/v1',
      ongarde_port: 4242,
    });
    const backupPath = getBackupPath();

    Object.keys(require.cache).filter(k => k.includes('openclaw/src')).forEach(k => delete require.cache[k]);

    const { runRollback } = require('../../src/commands/rollback');
    await runRollback();

    // Backup file should still exist
    expect(fs.existsSync(backupPath)).toBe(true);
  });

  test('rollback with missing config file returns error', async () => {
    const { createBackup } = require('../../src/lib/backup');
    createBackup({
      openclaw_config_path: '/nonexistent/config.json',
      original_base_url: 'https://api.openai.com/v1',
      ongarde_port: 4242,
    });

    Object.keys(require.cache).filter(k => k.includes('openclaw/src')).forEach(k => delete require.cache[k]);

    const { runRollback } = require('../../src/commands/rollback');
    const result = await runRollback();
    expect(result.success).toBe(false);
  });
});
