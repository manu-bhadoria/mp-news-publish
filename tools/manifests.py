#!/usr/bin/env python3
"""What each site serves now on Cloudflare, kept in the release asset manifests.tar.zst ({slug}.json per site).

A site's manifest is re-read when its live deployment is not the one the manifest was taken from:
  Pages sites:   the deployment's own file list (path -> wrangler hash)
  Workers sites: every file the laptop lists for the site (sites.tar.zst), fetched from the live site and hashed
With it go what the site engine needs without the files themselves: the names of the photos and posters under
assets/ (the engine checks they exist), the posters' link versions and their look-alike hashes.

    python3 tools/manifests.py [slug,slug]     # refresh these (all sites when none are given), then tell the portal
"""
import hashlib, io, json, os, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cfdeploy  # noqa: E402

WORK = Path(os.environ.get('WORK', HERE.parent / 'work')).resolve()
SITES = WORK / 'sites'
STORE = WORK / 'manifests'
PORTAL = os.environ.get('PORTAL_URL', 'https://mp-news-dashboard.nihilycho.workers.dev').rstrip('/')
POSTERS = ('/assets/shorts/', '/assets/fun/')


def deploys():
    return json.loads((SITES / 'deploy.json').read_text())


def stored(slug):
    f = STORE / f'{slug}.json'
    return json.loads(f.read_text()) if f.exists() else None


def save(slug, m):
    STORE.mkdir(parents=True, exist_ok=True)
    (STORE / f'{slug}.json').write_text(json.dumps(m))


def live_deployment(t):
    """The id of the deployment the site serves now."""
    if t['kind'] == 'pages':
        return cfdeploy.call(f'/accounts/{cfdeploy.ACC}/pages/projects/{t["name"]}')['canonical_deployment']['id']
    deps = cfdeploy.call(f'/accounts/{cfdeploy.ACC}/workers/scripts/{t["name"]}/deployments')
    return (deps.get('deployments') or [{}])[0].get('id')


def url_of(path):
    if path.endswith('/index.html'):
        return path[:-len('index.html')]
    if path.endswith('.html'):
        return path[:-5]
    return path


def get(url):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'mp-news-manifests'}), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            err = e
        except Exception as e:
            err = e
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f'{url}: {err}')


def thumb_hash(data):
    """core/portal.thumb_hash on bytes: an 8x8 average hash."""
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data)).convert('L').resize((8, 8))
        px = list(im.getdata())
        avg = sum(px) / 64
        return sum(1 << i for i, v in enumerate(px) if v > avg)
    except Exception:
        return None


def read_site(slug, t, url, listed, pool):
    """files {path: [hash, size]} plus the bytes of the posters, from the live site."""
    if t['kind'] == 'pages':
        dep = cfdeploy.call(f'/accounts/{cfdeploy.ACC}/pages/projects/{t["name"]}')['canonical_deployment']['id']
        files = {p: [h, None] for p, h in cfdeploy.call(f'/accounts/{cfdeploy.ACC}/pages/projects/{t["name"]}/deployments/{dep}')['files'].items()}
        want = [p for p in files if p.startswith(POSTERS) or (p.startswith('/assets/photos/yt-') and p.endswith('.webp'))]
        got = dict(zip(want, pool.map(lambda p: get(url + p), want)))
        return files, got
    got = dict(zip(listed, pool.map(lambda p: get(url + url_of(p)), listed)))
    files = {p: [cfdeploy.file_hash(b, p), len(b)] for p, b in got.items() if b is not None}
    return files, {p: b for p, b in got.items() if b is not None and (p.startswith(POSTERS) or (p.startswith('/assets/photos/yt-') and p.endswith('.webp')))}


def describe(slug, files, posters, headers, deployment):
    assets = sorted(p[len('/assets/'):] for p in files if p.startswith('/assets/') and p.count('/') >= 3)
    versions = {p[len('/assets/'):]: hashlib.sha256(b).hexdigest()[:12] for p, b in posters.items() if p.startswith(POSTERS)}
    looks = {}
    for p, b in posters.items():
        h = thumb_hash(b)
        if h is not None:
            looks[p[len('/assets/'):]] = h
    return {'files': files, 'deployment': deployment, 'captured': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'headers': headers, 'assets': assets, 'versions': versions, 'posters': looks}


def refresh(slug, pool, force=False):
    """Bring a site's manifest up to date with what it serves now. Returns (manifest, re-read?)."""
    t = deploys()[slug]
    info = json.loads((SITES / f'{slug}.json').read_text())
    dep = live_deployment(t)
    m = stored(slug)
    if m and not force and m.get('deployment') == dep and m.get('kind', t['kind']) == t['kind']:
        return m, False
    listed = sorted(set(info.get('paths') or []) | set((m or {}).get('files', {})) - {'/_headers'})
    files, posters = read_site(slug, t, t['url'].rstrip('/'), listed, pool)
    m = describe(slug, files, posters, info.get('headers'), dep)
    m['kind'] = t['kind']
    save(slug, m)
    return m, True


def after_deploy(slug, files, local):
    """The run just deployed this site: its manifest is the list it sent (posters as before, new photos added)."""
    t = deploys()[slug]
    old = stored(slug) or {}
    m = dict(old)
    m['files'] = files
    m['deployment'] = live_deployment(t)
    m['captured'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    m['assets'] = sorted(p[len('/assets/'):] for p in files if p.startswith('/assets/') and p.count('/') >= 3)
    save(slug, m)


def tell_portal(key):
    ready = sorted(s for s in deploys() if stored(s))
    req = urllib.request.Request(PORTAL + '/api/build/ready', data=json.dumps({'sites': ready}).encode(), method='POST',
                                 headers={'X-Build-Key': key, 'Content-Type': 'application/json', 'User-Agent': 'mp-news-manifests'})
    print('portal:', urllib.request.urlopen(req, timeout=60).read().decode()[:100], flush=True)


def main():
    want = [s for s in (sys.argv[1] if len(sys.argv) > 1 else '').split(',') if s]
    sites = sorted(deploys())
    targets = want or sites
    t0, changed, failed = time.time(), [], {}
    with ThreadPoolExecutor(48) as pool:
        def one(slug):
            try:
                m, again = refresh(slug, pool, force=slug in want)
                return slug, again, len(m['files']), None
            except Exception as e:
                return slug, False, 0, str(e)[:200]
        with ThreadPoolExecutor(6) as sites_pool:
            for slug, again, n, err in sites_pool.map(one, targets):
                if err:
                    failed[slug] = err
                    print(f'{slug}: FAILED {err}', flush=True)
                elif again:
                    changed.append(slug)
                    print(f'{slug}: {n} files ({round(time.time() - t0)}s)', flush=True)
    print(f'manifests: {len(changed)} re-read, {len(failed)} failed, {len(targets) - len(changed) - len(failed)} already current; {round(time.time() - t0)}s')
    if os.environ.get('BUILD_KEY'):
        tell_portal(os.environ['BUILD_KEY'])


if __name__ == '__main__':
    main()
