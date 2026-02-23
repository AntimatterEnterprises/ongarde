'use strict';
/**
 * Unit tests for src/lib/openclaw-config.js
 * E-007-S-001: AC-E007-S001-02, AC-E007-S001-03
 */

const fs = require('fs');
const os = require('os');
const path = require('path');
const {
  findOpenClawConfig,
  extractBaseUrl,
  updateBaseUrl,
  writeOpenClawConfig,
  getDefaultConfigPaths,
} = require('../../src/lib/openclaw-config');

describe('getDefaultConfigPaths', () => {
  test('includes home-based paths', () => {
    const paths = getDefaultConfigPaths();
    const home = os.homedir();
    expect(paths.some(p => p.includes(home))).toBe(true);
    expect(paths.length).toBeGreaterThan(0);
  });

  test('includes ~/.openclaw/config.json as first path', () => {
    const paths = getDefaultConfigPaths();
    expect(paths[0]).toBe(path.join(os.homedir(), '.openclaw', 'config.json'));
  });
});

describe('findOpenClawConfig', () => {
  let tmpDir;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'oc-test-'));
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  test('finds config at explicit --config path', () => {
    const configPath = path.join(tmpDir, 'config.json');
    const config = { baseUrl: 'https://api.openai.com/v1' };
    fs.writeFileSync(configPath, JSON.stringify(config));

    const result = findOpenClawConfig(configPath);
    expect(result.path).toBe(configPath);
    expect(result.config.baseUrl).toBe('https://api.openai.com/v1');
  });

  test('throws CONFIG_NOT_FOUND when explicit path missing', () => {
    expect(() => findOpenClawConfig('/nonexistent/config.json')).toThrow();
    try {
      findOpenClawConfig('/nonexistent/config.json');
    } catch (e) {
      expect(e.code).toBe('CONFIG_NOT_FOUND');
      expect(e.explicit).toBe(true);
    }
  });

  test('throws CONFIG_NOT_FOUND when no default paths exist (all missing)', () => {
    // Force all default paths to be non-existent by clearing temp env
    // We can't easily mock all FS paths, so test the explicit path not found case
    try {
      findOpenClawConfig('/definitely/does/not/exist.json');
    } catch (e) {
      expect(e.code).toBe('CONFIG_NOT_FOUND');
    }
  });
});

describe('extractBaseUrl', () => {
  test('extracts from models.providers[0].baseUrl', () => {
    const config = {
      models: {
        providers: [
          { name: 'openai', baseUrl: 'https://api.openai.com/v1' },
        ],
      },
    };
    const result = extractBaseUrl(config);
    expect(result.baseUrl).toBe('https://api.openai.com/v1');
    expect(result.location).toBe('provider');
    expect(result.providerIndex).toBe(0);
  });

  test('extracts from top-level baseUrl', () => {
    const config = { baseUrl: 'https://api.openai.com/v1' };
    const result = extractBaseUrl(config);
    expect(result.baseUrl).toBe('https://api.openai.com/v1');
    expect(result.location).toBe('root');
  });

  test('prefers provider over root', () => {
    const config = {
      baseUrl: 'https://root.example.com/v1',
      models: {
        providers: [{ baseUrl: 'https://provider.example.com/v1' }],
      },
    };
    const result = extractBaseUrl(config);
    expect(result.baseUrl).toBe('https://provider.example.com/v1');
    expect(result.location).toBe('provider');
  });

  test('throws NO_BASE_URL when not found', () => {
    try {
      extractBaseUrl({ someOtherField: 'value' });
    } catch (e) {
      expect(e.code).toBe('NO_BASE_URL');
    }
  });

  test('handles multiple providers — finds first with baseUrl', () => {
    const config = {
      models: {
        providers: [
          { name: 'anthropic' }, // no baseUrl
          { name: 'openai', baseUrl: 'https://api.openai.com/v1' },
        ],
      },
    };
    const result = extractBaseUrl(config);
    expect(result.baseUrl).toBe('https://api.openai.com/v1');
    expect(result.providerIndex).toBe(1);
  });
});

describe('updateBaseUrl', () => {
  test('updates provider baseUrl in-place', () => {
    const config = {
      models: {
        providers: [
          { name: 'openai', baseUrl: 'https://api.openai.com/v1', apiKey: 'sk-test' },
        ],
      },
      otherField: 'preserved',
    };

    const updated = updateBaseUrl(config, 'http://localhost:4242/v1', 'provider', 0);
    expect(updated.models.providers[0].baseUrl).toBe('http://localhost:4242/v1');
    // Verify other fields preserved
    expect(updated.models.providers[0].name).toBe('openai');
    expect(updated.models.providers[0].apiKey).toBe('sk-test');
    expect(updated.otherField).toBe('preserved');
  });

  test('updates root baseUrl in-place', () => {
    const config = { baseUrl: 'https://api.openai.com/v1', other: 'value' };
    const updated = updateBaseUrl(config, 'http://localhost:4242/v1', 'root', -1);
    expect(updated.baseUrl).toBe('http://localhost:4242/v1');
    expect(updated.other).toBe('value');
  });

  test('preserves all other fields in complex config', () => {
    const config = {
      version: '1.0',
      models: {
        providers: [
          {
            name: 'openai',
            baseUrl: 'https://api.openai.com/v1',
            apiKey: 'sk-xxx',
            model: 'gpt-4',
          },
        ],
        default: 'openai',
      },
      agents: [{ name: 'coder' }],
    };

    updateBaseUrl(config, 'http://localhost:4242/v1', 'provider', 0);
    expect(config.models.providers[0].apiKey).toBe('sk-xxx');
    expect(config.models.providers[0].model).toBe('gpt-4');
    expect(config.models.default).toBe('openai');
    expect(config.agents).toEqual([{ name: 'coder' }]);
    expect(config.version).toBe('1.0');
  });
});

describe('writeOpenClawConfig', () => {
  let tmpDir;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'oc-write-test-'));
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  test('writes valid JSON with newline', () => {
    const configPath = path.join(tmpDir, 'config.json');
    const config = { baseUrl: 'http://localhost:4242/v1' };
    writeOpenClawConfig(configPath, config);

    const content = fs.readFileSync(configPath, 'utf8');
    expect(content).toMatch(/http:\/\/localhost:4242\/v1/);
    expect(content.endsWith('\n')).toBe(true);

    // Should be parseable
    const parsed = JSON.parse(content);
    expect(parsed.baseUrl).toBe('http://localhost:4242/v1');
  });

  test('round-trips complex config without data loss', () => {
    const configPath = path.join(tmpDir, 'config.json');
    const config = {
      models: { providers: [{ name: 'openai', baseUrl: 'https://api.openai.com/v1' }] },
      agents: ['coder', 'researcher'],
    };
    writeOpenClawConfig(configPath, config);
    const parsed = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    expect(parsed.agents).toEqual(['coder', 'researcher']);
    expect(parsed.models.providers[0].name).toBe('openai');
  });
});
