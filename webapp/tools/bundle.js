/* Makes paste/Code.gs (all server files in one, to paste into the Apps Script editor). node tools/bundle.js */
'use strict';
const fs = require('fs');
const path = require('path');
const root = path.join(__dirname, '..');
const ORDER = ['Util', 'Config', 'Table', 'Auth', 'Db', 'Sdm', 'Attendance', 'Reservations', 'Work', 'Requests', 'Notices', 'Orders', 'Budget', 'Schedule', 'Main'];
const have = fs.readdirSync(path.join(root, 'server')).filter(f => f.endsWith('.gs')).map(f => f.slice(0, -3));
const missing = have.filter(f => !ORDER.includes(f));
if (missing.length) throw new Error('add to ORDER: ' + missing.join(', '));
let out = '/* Lab Web App server — one file to paste into Apps Script (Code.gs).\n * Made by tools/bundle.js from server/*.gs; edit those, not this. Holds private values: never commit. */\n';
// This lab's file ids and first-password hash: private/Private.gs (gitignored).
const priv = path.join(root, 'private', 'Private.gs');
if (fs.existsSync(priv)) out += '\n// ===== private/Private.gs =====\n' + fs.readFileSync(priv, 'utf8');
else console.warn('private/Private.gs not found: no default file ids or first password (copy private.example.gs)');
for (const f of ORDER) out += '\n// ===== ' + f + '.gs =====\n' + fs.readFileSync(path.join(root, 'server', f + '.gs'), 'utf8');
fs.mkdirSync(path.join(root, 'paste'), { recursive: true });
fs.writeFileSync(path.join(root, 'paste', 'Code.gs'), out);
fs.copyFileSync(path.join(root, 'server', 'appsscript.json'), path.join(root, 'paste', 'appsscript.json'));
console.log('paste/Code.gs', out.split('\n').length, 'lines');
