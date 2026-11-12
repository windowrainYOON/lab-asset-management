/* Local preview: serves web/ and answers /api with the real server code on made-up data.
 * node tests/devserver.js [port]  → http://localhost:8787/  (lab password: test-pin-42) */
'use strict';
const http = require('http');
const fs = require('fs');
const path = require('path');
const { load } = require('./harness');
const sample = require('./sample');

const WEB = path.join(__dirname, '..', 'web');
const port = +process.argv[2] || 8787;
const h = load(sample.options());
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.webmanifest': 'application/manifest+json' };

http.createServer((req, res) => {
  if (req.method === 'POST' && req.url === '/api') {
    let body = '';
    req.on('data', c => { body += c; });
    req.on('end', () => {
      let out;
      try { out = h.call(JSON.parse(body)); } catch (e) { out = { ok: false, error: String(e) }; }
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(out));
    });
    return;
  }
  const url = req.url.split('?')[0];
  if (url === '/js/config.js') {
    res.writeHead(200, { 'Content-Type': TYPES['.js'] });
    return res.end("window.LAB_CONFIG = { apiUrl: '/api' };");
  }
  const file = path.join(WEB, url === '/' ? 'index.html' : path.normalize(url).replace(/^(\.\.[/\\])+/, ''));
  if (!file.startsWith(WEB) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404); return res.end('not found'); }
  res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream' });
  fs.createReadStream(file).pipe(res);
}).listen(port, () => console.log('http://localhost:' + port + '/'));
module.exports = h;
