'use strict';
/**
 * Integration tests — error flows (don't require real proxy startup).
 * E-007-S-006: AC-E007-S006-05 (error scenarios)
 */

const fs = require('fs');
const {
  createTempOnGardeEnv,
  createMockOpenClawConfig,
  runCli,
  holdPort,
} = require('./helpers/setup');

describe('E-007 error flows', () => {
  let env;

  beforeEach(() => {
    env = createTempOnGardeEnv();
  });

  afterEach(() => {
    env.cleanup();
  });

  test('port conflict: exits 1 with E1 message (AC-E007-S001-06)', async () => {
    const mockConfigPath = createMockOpenClawConfig(env.tmpDir);
    const portHolder = await holdPort(14242); // Use a different port

    try {
      const result = await runCli(
        ['init', '--config', mockConfigPath, '--port', '14242', '--yes'],
        env.env
      );
      expect(result.code).toBe(1);
      expect(result.stdout).toMatch(/Port 14242 is already in use/);
      expect(result.stdout).toMatch(/--port/);
    } finally {
      await portHolder.close();
    }
  }, 15000);

  test('config not found: exits 1 with E2 message (AC-E007-S001-02)', async () => {
    const result = await runCli(
      ['init', '--config', '/nonexistent/path/config.json', '--yes'],
      env.env
    );
    expect(result.code).toBe(1);
    // Should show config not found error
    const combined = result.stdout + result.stderr;
    expect(combined).toMatch(/[Cc]onfig not found|not found/);
  }, 15000);

  test('second init run: backup not overwritten (AC-E007-S004-01)', async () => {
    const mockConfigPath = createMockOpenClawConfig(env.tmpDir);

    // Write a backup manually (simulating a previous run)
    const backupPath = require('path').join(env.tmpDir, 'openclaw-backup.json');
    fs.writeFileSync(backupPath, JSON.stringify({
      version: 1,
      created_at: '2026-04-06T12:00:00.000Z',
      openclaw_config_path: mockConfigPath,
      original_base_url: 'https://original.example.com/v1',
      ongarde_port: 4242,
    }, null, 2));

    // Run init — it should detect existing backup and not overwrite
    // (it will fail because proxy won't start, but backup should be preserved)
    const result = await runCli(
      ['init', '--config', mockConfigPath, '--port', '14243', '--yes'],
      { ...env.env, ONGARDE_DIR: '/nonexistent' } // force proxy spawn to fail quickly
    );

    // Verify backup still has original URL
    const backup = JSON.parse(fs.readFileSync(backupPath, 'utf8'));
    expect(backup.original_base_url).toBe('https://original.example.com/v1');
  }, 15000);

  test('rollback with no backup: exits 1 (AC-E007-S004-03)', async () => {
    const result = await runCli(['rollback'], env.env);
    expect(result.code).toBe(1);
    const combined = result.stdout + result.stderr;
    expect(combined).toMatch(/[Nn]o backup/);
  }, 10000);

  test('status when not running: exits 1 (AC-E007-S005-07)', async () => {
    const result = await runCli(['status', '--port', '59990'], env.env);
    expect(result.code).toBe(1);
  }, 10000);

  test('status --json outputs valid JSON (AC-E007-S005-08)', async () => {
    const result = await runCli(['status', '--port', '59989', '--json'], env.env);
    expect(result.code).toBe(1); // proxy not running
    try {
      const parsed = JSON.parse(result.stdout);
      expect(parsed).toHaveProperty('proxy');
      expect(parsed).toHaveProperty('port');
      expect(parsed).toHaveProperty('dashboard');
    } catch {
      fail('JSON output is not valid JSON: ' + result.stdout);
    }
  }, 10000);

  test('--help exits 0 and shows usage', async () => {
    const result = await runCli(['--help'], env.env);
    expect(result.code).toBe(0);
    expect(result.stdout).toContain('init');
    expect(result.stdout).toContain('start');
    expect(result.stdout).toContain('status');
  }, 10000);

  test('unknown command exits 1', async () => {
    const result = await runCli(['nonexistent-command'], env.env);
    expect(result.code).toBe(1);
    expect(result.stdout + result.stderr).toMatch(/[Uu]nknown command/);
  }, 10000);
});
