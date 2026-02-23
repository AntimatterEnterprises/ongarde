'use strict';
/**
 * Integration test helpers.
 * E-007-S-006: test environment setup/teardown.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');
const { execSync, spawn } = require('child_process');

/**
 * Create an isolated OnGarde test environment.
 * Returns cleanup function and env vars.
 */
function createTempOnGardeEnv() {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ongarde-e2e-'));
  return {
    tmpDir,
    env: {
      ONGARDE_HOME: tmpDir,
      ONGARDE_TEST_MODE: '1',
    },
    cleanup: () => {
      try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch {}
    },
  };
}

/**
 * Create a minimal mock OpenClaw config.
 */
function createMockOpenClawConfig(dir, baseUrl = 'https://api.openai.com/v1') {
  const config = {
    models: {
      providers: [{
        name: 'openai',
        baseUrl,
        apiKey: 'sk-test-fake-key-for-testing',
      }],
    },
  };
  const configPath = path.join(dir, 'openclaw-config.json');
  fs.writeFileSync(configPath, JSON.stringify(config, null, 2));
  return configPath;
}

/**
 * Timing gate for measuring end-to-end flow duration.
 */
class TimingGate {
  constructor(limitMs = 300000) {
    this.start = Date.now();
    this.checkpoints = [];
    this.limitMs = limitMs;
  }

  mark(name) {
    const elapsed = Date.now() - this.start;
    this.checkpoints.push({ name, elapsed });
    console.log(`[timing] step:${name.padEnd(20)} elapsed: ${(elapsed / 1000).toFixed(1)}s`);
  }

  assertUnderLimit() {
    const total = Date.now() - this.start;
    const totalS = (total / 1000).toFixed(1);
    const limitS = (this.limitMs / 1000).toFixed(0);
    if (total > this.limitMs) {
      throw new Error(`TIMING GATE FAILED: ${totalS}s > ${limitS}s limit`);
    }
    console.log(`[timing] PASS: ${totalS}s < ${limitS}s`);
  }
}

/**
 * Run the CLI as a child process and capture output.
 */
function runCli(args, env = {}, cwd = null) {
  const cliPath = path.resolve(__dirname, '../../../bin/ongarde.js');
  return new Promise((resolve, reject) => {
    const proc = spawn(process.execPath, [cliPath, ...args], {
      env: { ...process.env, ...env },
      cwd: cwd || path.resolve(__dirname, '../../../'),
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';

    proc.stdout.on('data', d => { stdout += d; });
    proc.stderr.on('data', d => { stderr += d; });

    proc.on('close', code => resolve({ code, stdout, stderr }));
    proc.on('error', reject);

    // Auto-feed Enter for interactive prompts
    setTimeout(() => {
      try { proc.stdin.write('\n'); } catch {}
    }, 500);
  });
}

/**
 * Check if Python 3.12+ is available.
 */
function checkPython() {
  try {
    const result = execSync('python3 --version', { encoding: 'utf8', stdio: 'pipe' });
    const match = result.match(/Python (\d+)\.(\d+)/);
    if (match) {
      const major = parseInt(match[1], 10);
      const minor = parseInt(match[2], 10);
      return major > 3 || (major === 3 && minor >= 12);
    }
  } catch {}
  return false;
}

/**
 * Create a TCP server that holds port N.
 */
function holdPort(port) {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      res.writeHead(200); res.end('port holder');
    });
    server.listen(port, '127.0.0.1', () => {
      resolve({
        server,
        close: () => new Promise(r => server.close(r)),
      });
    });
  });
}

module.exports = {
  createTempOnGardeEnv,
  createMockOpenClawConfig,
  TimingGate,
  runCli,
  checkPython,
  holdPort,
};
