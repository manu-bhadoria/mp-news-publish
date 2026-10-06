"""Deploy a static site to Cloudflare (Pages project or Workers static assets) from an asset manifest plus the files
that changed, the same requests wrangler makes. The manifest lists every file the site serves with wrangler's hash
(blake3 of base64 content + extension, 32 hex); only files Cloudflare does not have yet are uploaded."""
import base64, json, mimetypes, os, time, urllib.error, urllib.request, uuid
from pathlib import Path

import blake3

API = 'https://api.cloudflare.com/client/v4'
ACC = os.environ.get('CLOUDFLARE_ACCOUNT_ID', '')
TOKEN = os.environ.get('CLOUDFLARE_API_TOKEN', '')
SPECIAL = {'/_headers', '/_redirects', '/_routes.json', '/_worker.js'}
TYPES = {'.webp': 'image/webp', '.woff2': 'font/woff2', '.woff': 'font/woff', '.webmanifest': 'application/manifest+json',
         '.json': 'application/json', '.xml': 'application/xml', '.svg': 'image/svg+xml', '.html': 'text/html',
         '.css': 'text/css', '.js': 'application/javascript', '.txt': 'text/plain', '.ico': 'image/x-icon',
         '.avif': 'image/avif', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif'}


class Missing(Exception):
    """Cloudflare asked for a file this run does not have: the stored manifest is older than the live site."""


def file_hash(data, name):
    ext = os.path.splitext(name)[1][1:]
    return blake3.blake3(base64.b64encode(data) + ext.encode()).hexdigest()[:32]


def ctype(path):
    ext = os.path.splitext(path)[1].lower()
    return TYPES.get(ext) or mimetypes.guess_type(path)[0] or 'application/octet-stream'


def call(path, method='GET', body=None, token=None, headers=None, raw=False, tries=5):
    url = path if path.startswith('http') else API + path
    h = {'Authorization': 'Bearer ' + (token or TOKEN), **(headers or {})}
    data = body
    if isinstance(body, (dict, list)):
        data = json.dumps(body).encode()
        h['Content-Type'] = 'application/json'
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, method=method, headers=h), timeout=300) as r:
                out = json.loads(r.read() or b'{}')
                return out if raw else out.get('result')
        except urllib.error.HTTPError as e:
            msg = e.read().decode('utf-8', 'replace')[:500]
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            raise RuntimeError(f'{method} {path.split("?")[0]} -> {e.code}: {msg}')
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < tries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            raise RuntimeError(f'{method} {path.split("?")[0]}: {e}')


def multipart(fields):
    """fields: [(name, filename or None, content-type or None, bytes)]"""
    b = uuid.uuid4().hex
    out = []
    for name, filename, ct, data in fields:
        disp = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else '')
        out.append(f'--{b}\r\nContent-Disposition: {disp}\r\n' + (f'Content-Type: {ct}\r\n' if ct else '') + '\r\n')
        out.append(data)
        out.append('\r\n')
    out.append(f'--{b}--\r\n')
    body = b''.join(x.encode() if isinstance(x, str) else x for x in out)
    return body, {'Content-Type': f'multipart/form-data; boundary={b}'}


def deploy(target, files, local, headers_text=None):
    """target: {'kind': 'worker'|'pages', 'name': ...}; files: {'/path': [hash, size]} (the whole site);
    local: {hash: (path, bytes)} for the files this run made. Returns how many files were uploaded."""
    files = {p: v for p, v in files.items() if p not in SPECIAL}
    if target['kind'] == 'worker':
        return _worker(target['name'], files, local, headers_text)
    return _pages(target['name'], files, local, headers_text)


def _need(hashes, local):
    lost = [h for h in hashes if h not in local]
    if lost:
        raise Missing(f'{len(lost)} पुरानी फाइलें क्लाउड के पास नहीं हैं (लैपटॉप से साइट दोबारा सिंक करें)')


def _worker(name, files, local, headers_text):
    s = call(f'/accounts/{ACC}/workers/scripts/{name}/assets-upload-session', 'POST',
             {'manifest': {p: {'hash': h, 'size': n} for p, (h, n) in files.items()}})
    jwt, buckets = s['jwt'], s.get('buckets') or []
    _need([h for b in buckets for h in b], local)
    done = jwt
    for bucket in buckets:
        if not bucket:
            continue
        parts = [(h, h, ctype(local[h][0]), base64.b64encode(local[h][1])) for h in bucket]
        body, hdr = multipart(parts)
        r = call(f'/accounts/{ACC}/workers/assets/upload?base64=true', 'POST', body, token=jwt, headers=hdr, raw=True)
        done = (r.get('result') or {}).get('jwt') or done
    config = {'html_handling': 'auto-trailing-slash', 'not_found_handling': '404-page'}
    if headers_text:
        config['_headers'] = headers_text
    meta = {'assets': {'jwt': done, 'config': config}, 'compatibility_date': '2026-09-15'}
    body, hdr = multipart([('metadata', None, 'application/json', json.dumps(meta).encode())])
    call(f'/accounts/{ACC}/workers/scripts/{name}?excludeScript=true&bindings_inherit=strict', 'PUT', body, headers=hdr)
    return sum(len(b) for b in buckets)


def _pages(project, files, local, headers_text):
    jwt = call(f'/accounts/{ACC}/pages/projects/{project}/upload-token')['jwt']
    hashes = sorted({h for h, _ in files.values()})
    missing = call('/pages/assets/check-missing', 'POST', {'hashes': hashes}, token=jwt) or []
    _need(missing, local)
    batch, size = [], 0
    for h in missing + [None]:
        if h is not None:
            path, data = local[h]
            batch.append({'key': h, 'value': base64.b64encode(data).decode(), 'metadata': {'contentType': ctype(path)}, 'base64': True})
            size += len(data)
        if batch and (h is None or size > 20_000_000 or len(batch) >= 1000):
            call('/pages/assets/upload', 'POST', batch, token=jwt)
            batch, size = [], 0
    call('/pages/assets/upsert-hashes', 'POST', {'hashes': hashes}, token=jwt)
    fields = [('manifest', None, None, json.dumps({p: h for p, (h, _) in files.items()}).encode()), ('branch', None, None, b'main')]
    if headers_text:
        fields.append(('_headers', '_headers', 'text/plain', headers_text.encode()))
    body, hdr = multipart(fields)
    call(f'/accounts/{ACC}/pages/projects/{project}/deployments', 'POST', body, headers=hdr)
    return len(missing)


def hash_tree(root):
    """{'/path': [hash, size]} and {hash: (path, bytes)} for every file under root."""
    root = Path(root)
    files, local = {}, {}
    for f in sorted(root.rglob('*')):
        if f.is_file() and not f.name.startswith('.'):
            data = f.read_bytes()
            rel = '/' + f.relative_to(root).as_posix()
            h = file_hash(data, f.name)
            files[rel] = [h, len(data)]
            local[h] = (rel, data)
    return files, local
