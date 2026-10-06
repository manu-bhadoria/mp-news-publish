#!/usr/bin/env python3
"""One publishing run, started by the portal (mp-news-dashboard Worker) through workflow_dispatch.

1. Claim the waiting work from the portal: which stories go on (or come off) which sites.
2. Rebuild just the changed pages of those sites with the sites' own engine (Publication.build_partial), from the
   inputs the laptop last synced (release "state": inputs.tar.zst + manifests.tar.zst).
3. Deploy each site from its stored asset manifest plus the rebuilt pages (only new files are uploaded).
4. Open every story link on the live site, then report each site back to the portal.

Every portal story that is live on a site is rebuilt each time, so the stored manifests (what the laptop last
uploaded) never need to be written back from here.
"""
import json, os, shutil, sys, tarfile, time, traceback, urllib.error, urllib.request
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cfdeploy  # noqa: E402

PORTAL = os.environ.get('PORTAL_URL', 'https://mp-news-dashboard.nihilycho.workers.dev').rstrip('/')
KEY = os.environ.get('BUILD_KEY', '')
RUN = os.environ.get('GITHUB_RUN_ID') or str(int(time.time()))
WORK = Path(os.environ.get('WORK', HERE.parent / 'work')).resolve()
REPO = WORK / 'tree'                         # the laptop's repo layout: REPO/newsroom/..., REPO/news-updates.json
NEWS = REPO / 'newsroom'
OUT = WORK / 'out'


def portal(path, body=None):
    req = urllib.request.Request(PORTAL + path, data=json.dumps(body).encode() if body is not None else None,
                                 method='POST' if body is not None else 'GET',
                                 headers={'X-Build-Key': KEY, 'Content-Type': 'application/json', 'User-Agent': 'mp-news-publish'})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == 4:
                raise
            print('portal retry', path, e, flush=True)
            time.sleep(3 * (attempt + 1))


def fetch(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'mp-news-publish-check', 'Cache-Control': 'no-cache'}), timeout=timeout) as r:
        return r.status, r.read()


def unpack():
    for name in ('inputs.tar.zst', 'manifests.tar.zst'):
        src = WORK / name
        dest = REPO if name.startswith('inputs') else WORK / 'manifests'
        dest.mkdir(parents=True, exist_ok=True)
        os.system(f'zstd -dq --stdout "{src}" | tar -x -C "{dest}"')


def photos(live):
    """Portal photos the stories use, from the portal; JPEGs (browsers that cannot write WebP) become WebP like the rest."""
    from PIL import Image
    media = NEWS / 'portal' / 'media'
    media.mkdir(parents=True, exist_ok=True)
    for st in live:
        p = st.get('photo')
        if not p:
            continue
        for v in p['variants']:
            dst = media / v['file']
            if not dst.exists():
                code, data = fetch(PORTAL + '/media/' + v['file'], timeout=60)
                dst.write_bytes(data)
            if dst.suffix == '.jpg':
                webp = dst.with_suffix('.webp')
                Image.open(dst).convert('RGB').save(webp, 'WEBP', quality=84, method=5)
                v['file'], v['bytes'] = webp.name, webp.stat().st_size


def on_site(st, slug):
    return not st.get('sites') or slug in st['sites']


def build_one(args):
    """Rebuild a site's changed pages into an empty folder (child process: the engine imports per site)."""
    slug, ids, removed, topics, places = args
    sys.path.insert(0, str(NEWS))
    t = time.time()
    try:
        from core.publishing import Publication, portal_feed
        out = OUT / slug
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        (out / 'index.html').write_text('')        # build_partial does a full build into an empty folder; it rewrites this page
        pub = Publication(slug)
        tops = {portal_feed().site_topic(pub.brand['topics'], x)[0] for x in topics}
        pub.build_partial(out, ids=ids, removed=removed, topics=tops, places=set(places) | {'all'})
        return slug, None, {i: pub.url(i) for i in ids + removed}, round(time.time() - t, 1)
    except Exception:
        return slug, traceback.format_exc()[-700:], {}, round(time.time() - t, 1)


def check(url, home_url, title, path):
    """The story page answers with the headline, and the home page links to it (a few tries: the upload settles in seconds)."""
    piece = max(title.split(': '), key=len)[:40]
    import html as H
    err = 'पेज नहीं खुला'
    for attempt in range(6):
        try:
            code, page = fetch(url)
            text = page.decode('utf-8', 'replace')
            if code == 200 and (H.escape(piece, quote=False) in text or piece in text):
                try:
                    home = fetch(home_url)[1].decode('utf-8', 'replace')
                    on_home = path in home or path.lstrip('/') in home
                except Exception:
                    on_home = False
                return [True, on_home, None]
            err = 'पेज पर हेडलाइन नहीं मिली'
        except Exception as e:
            err = f'पेज नहीं खुला: {e}'
        time.sleep(5)
    return [False, False, err]


def main():
    if not KEY:
        sys.exit('BUILD_KEY missing')
    claim = portal('/api/build/claim', {'run': RUN})
    plan = claim['plan']
    print(f'run {RUN}: jobs {claim["jobs"]}, sites {len(plan)}', flush=True)
    if not plan:
        portal('/api/build/finish', {'run': RUN, 'ok': True})
        return
    try:
        unpack()
        deploys = json.loads((WORK / 'manifests' / 'deploy.json').read_text())
        live = claim['live']
        photos(live)
        data = NEWS / 'portal' / 'data'
        data.mkdir(parents=True, exist_ok=True)
        (data / 'live.json').write_text(json.dumps({'stories': live}, ensure_ascii=False))
        all_ids = [s['id'] for s in live] + claim['removed']
        by_id = {s['id']: s for s in live}
        jobs, skipped = [], {}
        for slug in plan:
            if slug not in deploys or deploys[slug].get('legacy'):
                skipped[slug] = 'यह साइट अभी क्लाउड से नहीं छपती'
                continue
            ids = [s['id'] for s in live if on_site(s, slug)]
            jobs.append((slug, ids, [i for i in all_ids if i not in ids], claim['topics'], claim['places']))
        for slug, why in skipped.items():
            portal('/api/build/report', {'site': slug, 'on': plan[slug]['on'], 'off': plan[slug]['off'], 'ok': False, 'error': why})

        def ship(res):
            slug, err, urls, secs = res
            p = plan[slug]
            if err:
                print(f'{slug}: build failed\n{err}', flush=True)
                return portal('/api/build/report', {'site': slug, 'on': p['on'], 'off': p['off'], 'ok': False, 'error': 'बिल्ड नहीं हुआ: ' + err[-200:]})
            try:
                m = json.loads((WORK / 'manifests' / f'{slug}.json').read_text())
                files = dict(m['files'])
                gone = [urls[i] for i in urls if i not in by_id or not on_site(by_id[i], slug)]
                for path in list(files):                 # story folders of stories no longer on this site
                    if any(g != '/' and path.startswith(g) for g in gone):
                        del files[path]
                new, local = cfdeploy.hash_tree(OUT / slug)
                files.update(new)
                n = cfdeploy.deploy(deploys[slug], files, local, m.get('headers'))
                origin = deploys[slug]['url'].rstrip('/')
                checks = {sid: check(origin + urls[sid], origin + '/', by_id[sid]['title'], urls[sid]) for sid in p['on']}
                bad = [sid for sid, c in checks.items() if not c[0]]
                print(f'{slug}: built {secs}s, uploaded {n} files, {len(checks) - len(bad)}/{len(checks)} links ok', flush=True)
                portal('/api/build/report', {'site': slug, 'on': p['on'], 'off': p['off'], 'ok': True, 'checks': checks})
            except Exception as e:
                print(f'{slug}: deploy failed: {e}', flush=True)
                portal('/api/build/report', {'site': slug, 'on': p['on'], 'off': p['off'], 'ok': False, 'error': f'अपलोड नहीं हुआ: {str(e)[-200:]}'})

        with ProcessPoolExecutor(os.cpu_count() or 2) as builds, ThreadPoolExecutor(6) as uploads:
            shipped = [uploads.submit(ship, f.result()) for f in as_completed([builds.submit(build_one, j) for j in jobs])]
            for f in shipped:
                f.result()
        portal('/api/build/finish', {'run': RUN, 'ok': True})
    except Exception as e:
        traceback.print_exc()
        portal('/api/build/finish', {'run': RUN, 'ok': False, 'error': f'{type(e).__name__}: {e}'[:300]})
        raise


if __name__ == '__main__':
    main()
