'use strict';
/**
 * Unit tests for src/lib/health.js
 * E-007-S-002: AC-E007-S002-01 through AC-E007-S002-03
 */

const http = require('http');
const { pollUntilReady, checkHealth, sleep } = require('../../src/lib/health');

/**
 * Create a simple mock HTTP server that serves health responses.
 *
 * @param {number[]} statusSequence - Sequence of status codes to return
 * @returns {{ server, port, close }}
 */
function createMockServer(statusSequence) {
  let callCount = 0;
  const server = http.createServer((req, res) => {
    const status = statusSequence[Math.min(callCount, statusSequence.length - 1)];
    callCount++;
    const body = status === 200
      ? JSON.stringify({ status: 'ok', proxy: 'running', scanner: 'healthy' })
      : JSON.stringify({ error: { status: 'starting' } });
    res.writeHead(status, { 'Content-Type': 'application/json' });
    res.end(body);
  });

  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      resolve({
        server,
        port,
        callCount: () => callCount,
        close: () => new Promise(r => server.close(r)),
      });
    });
  });
}

describe('pollUntilReady', () => {
  test('returns ready=true when server immediately returns 200', async () => {
    const mock = await createMockServer([200]);
    try {
      const result = await pollUntilReady({
        port: mock.port,
        timeoutMs: 5000,
        intervalMs: 100,
      });
      expect(result.ready).toBe(true);
      expect(result.response).toMatchObject({ status: 'ok' });
    } finally {
      await mock.close();
    }
  });

  test('polls through 503s then returns ready on 200', async () => {
    // 503, 503, 200
    const mock = await createMockServer([503, 503, 200]);
    try {
      const result = await pollUntilReady({
        port: mock.port,
        timeoutMs: 5000,
        intervalMs: 50,
      });
      expect(result.ready).toBe(true);
      expect(mock.callCount()).toBeGreaterThanOrEqual(3);
    } finally {
      await mock.close();
    }
  });

  test('returns ready=false on timeout', async () => {
    // Always returns 503
    const mock = await createMockServer([503]);
    try {
      const result = await pollUntilReady({
        port: mock.port,
        timeoutMs: 300,
        intervalMs: 50,
      });
      expect(result.ready).toBe(false);
      expect(result.reason).toBe('timeout');
    } finally {
      await mock.close();
    }
  });

  test('returns ready=false when server not listening (ECONNREFUSED)', async () => {
    // Port 59997 should have nothing listening
    const result = await pollUntilReady({
      port: 59997,
      timeoutMs: 300,
      intervalMs: 50,
    });
    expect(result.ready).toBe(false);
    expect(result.reason).toBe('timeout');
  });

  test('aborts when shouldAbort returns true', async () => {
    const mock = await createMockServer([503, 503, 503]);
    let abortCalled = 0;
    try {
      const result = await pollUntilReady({
        port: mock.port,
        timeoutMs: 5000,
        intervalMs: 50,
        shouldAbort: () => {
          abortCalled++;
          return abortCalled > 2;
        },
      });
      expect(result.ready).toBe(false);
      expect(result.reason).toBe('aborted');
    } finally {
      await mock.close();
    }
  });

  test('polls every intervalMs (timing check)', async () => {
    const mock = await createMockServer([503, 503, 503, 200]);
    try {
      const start = Date.now();
      const result = await pollUntilReady({
        port: mock.port,
        timeoutMs: 5000,
        intervalMs: 100,
      });
      const elapsed = Date.now() - start;
      expect(result.ready).toBe(true);
      // Should have taken ~300ms (3 intervals of 100ms) - with some tolerance
      expect(elapsed).toBeGreaterThan(200);
      expect(elapsed).toBeLessThan(2000);
    } finally {
      await mock.close();
    }
  });
});

describe('checkHealth', () => {
  test('returns ok=true for HTTP 200', async () => {
    const mock = await createMockServer([200]);
    try {
      const result = await checkHealth(mock.port);
      expect(result.ok).toBe(true);
      expect(result.status).toBe(200);
      expect(result.body).toMatchObject({ status: 'ok' });
    } finally {
      await mock.close();
    }
  });

  test('returns ok=false for HTTP 503', async () => {
    const mock = await createMockServer([503]);
    try {
      const result = await checkHealth(mock.port);
      expect(result.ok).toBe(false);
      expect(result.status).toBe(503);
    } finally {
      await mock.close();
    }
  });

  test('returns ok=false when server not available', async () => {
    const result = await checkHealth(59996);
    expect(result.ok).toBe(false);
    expect(result.status).toBeNull();
  });
});

describe('sleep', () => {
  test('resolves after given ms', async () => {
    const start = Date.now();
    await sleep(100);
    const elapsed = Date.now() - start;
    expect(elapsed).toBeGreaterThanOrEqual(80);
    expect(elapsed).toBeLessThan(500);
  });
});
