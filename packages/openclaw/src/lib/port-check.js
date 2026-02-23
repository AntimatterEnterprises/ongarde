'use strict';
/**
 * TCP port availability check.
 * Used by init and start commands to detect port conflicts.
 */

const net = require('net');

/**
 * Check if a TCP port is already in use on localhost.
 *
 * @param {number} port - Port number to check (1024–65535)
 * @param {string} [host='127.0.0.1'] - Host to check
 * @param {number} [timeoutMs=2000] - Connection timeout
 * @returns {Promise<boolean>} true if port is in use, false if available
 */
function isPortInUse(port, host = '127.0.0.1', timeoutMs = 2000) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    let settled = false;

    const done = (inUse) => {
      if (!settled) {
        settled = true;
        socket.destroy();
        resolve(inUse);
      }
    };

    socket.setTimeout(timeoutMs);
    socket.on('connect', () => done(true));     // connected = port in use
    socket.on('timeout', () => done(false));    // timeout = nothing listening
    socket.on('error', (err) => {
      if (err.code === 'ECONNREFUSED') {
        done(false);  // refused = port available
      } else {
        done(false);  // other error = treat as available
      }
    });

    socket.connect(port, host);
  });
}

/**
 * Validate a port number.
 * @param {*} value - Value to validate
 * @returns {number} Validated port number
 * @throws {Error} If port is invalid
 */
function validatePort(value) {
  const port = parseInt(value, 10);
  if (isNaN(port) || port < 1024 || port > 65535) {
    throw new Error(`Invalid port: ${value}. Must be an integer between 1024 and 65535.`);
  }
  return port;
}

module.exports = { isPortInUse, validatePort };
