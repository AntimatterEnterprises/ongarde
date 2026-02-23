'use strict';
/**
 * Unit tests for src/lib/backup.js
 * E-007-S-004: AC-E007-S004-01 through AC-E007-S004-07
 */

const fs = require('fs');
const os = require('os');
const path = require('path');

describe('backup.js', () => {
  let tmpDir;
  let origEnv;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ongarde-backup-test-'));
    origEnv = process.env.ONGARDE_HOME;
    process.env.ONGARDE_HOME = tmpDir;

    // Clear module cache to pick up new ONGARDE_HOME
    delete require.cache[require.resolve('../../src/lib/backup')];
  });

  afterEach(() => {
    if (origEnv === undefined) {
      delete process.env.ONGARDE_HOME;
    } else {
      process.env.ONGARDE_HOME = origEnv;
    }
    fs.rmSync(tmpDir, { recursive: true, force: true });
    delete require.cache[require.resolve('../../src/lib/backup')];
  });

  function getBackup() {
    return require('../../src/lib/backup');
  }

  test('getOnGardeHome returns ONGARDE_HOME when set', () => {
    const { getOnGardeHome } = getBackup();
    expect(getOnGardeHome()).toBe(tmpDir);
  });

  test('getOnGardeHome returns ~/.ongarde by default', () => {
    delete process.env.ONGARDE_HOME;
    delete require.cache[require.resolve('../../src/lib/backup')];
    const { getOnGardeHome } = require('../../src/lib/backup');
    expect(getOnGardeHome()).toBe(path.join(os.homedir(), '.ongarde'));
    process.env.ONGARDE_HOME = tmpDir;
  });

  test('createBackup writes backup atomically', () => {
    const { createBackup, getBackupPath, readBackup } = getBackup();
    const data = {
      openclaw_config_path: '/home/user/.openclaw/config.json',
      original_base_url: 'https://api.openai.com/v1',
      ongarde_port: 4242,
    };

    const created = createBackup(data);
    expect(created).toBe(true);
    expect(fs.existsSync(getBackupPath())).toBe(true);

    const backup = readBackup();
    expect(backup.openclaw_config_path).toBe('/home/user/.openclaw/config.json');
    expect(backup.original_base_url).toBe('https://api.openai.com/v1');
    expect(backup.version).toBe(1);
    expect(backup.created_at).toBeTruthy();
  });

  test('createBackup does not overwrite existing backup (AC-E007-S004-02)', () => {
    const { createBackup, readBackup } = getBackup();

    // Create first backup
    createBackup({ original_base_url: 'https://original.example.com/v1', ongarde_port: 4242 });

    // Try to overwrite — should return false
    const result = createBackup({ original_base_url: 'https://new.example.com/v1', ongarde_port: 4242 });
    expect(result).toBe(false);

    // Original data preserved
    const backup = readBackup();
    expect(backup.original_base_url).toBe('https://original.example.com/v1');
  });

  test('backup file has 0600 permissions on non-Windows (AC-E007-S004-02)', () => {
    if (process.platform === 'win32') return;
    const { createBackup, getBackupPath } = getBackup();
    createBackup({ original_base_url: 'https://api.openai.com/v1', ongarde_port: 4242 });
    const stat = fs.statSync(getBackupPath());
    const perms = stat.mode & 0o777;
    expect(perms).toBe(0o600);
  });

  test('readBackup returns null when file does not exist', () => {
    const { readBackup } = getBackup();
    expect(readBackup()).toBeNull();
  });

  test('writeState and readState round-trip', () => {
    const { writeState, readState } = getBackup();
    const data = {
      api_key_id: '01HXQ7F9V8K5M3N2P0R4T6W8Y1',
      api_key_masked: '...8Y1',
      ongarde_port: 4242,
    };
    writeState(data);
    const state = readState();
    expect(state.api_key_id).toBe('01HXQ7F9V8K5M3N2P0R4T6W8Y1');
    expect(state.api_key_masked).toBe('...8Y1');
    expect(state.version).toBe(1);
  });

  test('readState returns null when file does not exist', () => {
    const { readState } = getBackup();
    expect(readState()).toBeNull();
  });

  test('writePid and readPid round-trip', () => {
    const { writePid, readPid } = getBackup();
    writePid(12345);
    expect(readPid()).toBe(12345);
  });

  test('readPid returns null when file does not exist', () => {
    const { readPid } = getBackup();
    expect(readPid()).toBeNull();
  });

  test('deletePid removes PID file', () => {
    const { writePid, readPid, deletePid, getPidPath } = getBackup();
    writePid(999);
    expect(fs.existsSync(getPidPath())).toBe(true);
    deletePid();
    expect(fs.existsSync(getPidPath())).toBe(false);
  });

  test('isPidActive returns false for non-existent PID', () => {
    const { isPidActive } = getBackup();
    // PID 999999999 is almost certainly not running
    expect(isPidActive(999999999)).toBe(false);
  });

  test('isPidActive returns true for current process', () => {
    const { isPidActive } = getBackup();
    expect(isPidActive(process.pid)).toBe(true);
  });

  test('backupExists returns false when no backup', () => {
    const { backupExists } = getBackup();
    expect(backupExists()).toBe(false);
  });

  test('backupExists returns true after createBackup', () => {
    const { createBackup, backupExists } = getBackup();
    createBackup({ original_base_url: 'https://api.openai.com/v1', ongarde_port: 4242 });
    expect(backupExists()).toBe(true);
  });
});
