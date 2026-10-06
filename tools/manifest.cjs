// Asset manifest of a built site, hashed exactly as wrangler does (blake3 of base64 content + extension, 32 hex):
//   node manifest.cjs <dir> [<dir>...]  ->  {"<dir>": {"/path": [hash, size]}}  on stdout
// Pages and Workers static assets use the same hash, so the cloud can deploy a site from this list plus the changed files.
const fs = require('fs'), path = require('path');
let blake3;
try { blake3 = require('blake3-wasm'); } catch { blake3 = require(require.resolve('blake3-wasm', { paths: [process.env.WRANGLER_MODULES || '.'] })); }
const SKIP = new Set(['_worker.js', '_routes.json']);
function walk(root, dir, out) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(root, p, out);
    else if (e.isFile() && !(dir === root && SKIP.has(e.name)) && !e.name.startsWith('.')) {
      const buf = fs.readFileSync(p);
      const rel = '/' + path.relative(root, p).split(path.sep).join('/');
      out[rel] = [blake3.hash(buf.toString('base64') + path.extname(p).slice(1)).toString('hex').slice(0, 32), buf.length];
    }
  }
  return out;
}
const res = {};
for (const d of process.argv.slice(2)) res[d] = walk(path.resolve(d), path.resolve(d), {});
process.stdout.write(JSON.stringify(res));
