"""Content sharing over Tailscale: the host's AC Link serves the cars and track of the game it is hosting, the guest's
AC Link downloads whatever it is missing or has in a different version.

Host: a small read-only HTTP server on the host's Tailscale address, port = AssettoServer HTTP port + 1. It answers
only Tailscale addresses (100.64.0.0/10), only while hosting, and only for files inside the hosted cars' and track's
folders. Official Kunos content (base game and DLC) is never offered: the guest has it from Steam or has to buy it.
  GET /manifest           {'items': [{kind, id, name, files: [[rel, size]], total, check: {rel: md5}}], 'skipped': [...]}
  GET /file?k=&id=&p=     one file, with Range support so an interrupted download resumes

Guest: compare() marks each item ok / missing / different (the files AC checksums differ, so AC would refuse to join),
fetch() downloads into content/<kind>/<id> (new items are staged in a hidden folder and renamed when complete)."""
import hashlib, ipaddress, json, os, re, shutil, threading, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TAILNET = ipaddress.ip_network('100.64.0.0/10')

# base-game content without the ks_ prefix (everything newer from Kunos starts with ks_)
KUNOS_CARS = set('''abarth500 abarth500_s1 bmw_1m bmw_1m_s3 bmw_m3_e30 bmw_m3_e30_drift bmw_m3_e30_dtm bmw_m3_e30_gra
bmw_m3_e30_s1 bmw_m3_e92 bmw_m3_e92_drift bmw_m3_e92_s1 bmw_m3_gt2 bmw_z4 bmw_z4_drift bmw_z4_gt3 bmw_z4_s1 ferrari_312t
ferrari_458 ferrari_458_gt2 ferrari_458_s3 ferrari_599xxevo ferrari_f40 ferrari_f40_s3 ferrari_laferrari ktm_xbow_r
lotus_2_eleven lotus_2_eleven_gt4 lotus_49 lotus_98t lotus_elise_sc lotus_elise_sc_s1 lotus_elise_sc_s2 lotus_evora_gtc
lotus_evora_gte lotus_evora_gte_carbon lotus_evora_gx lotus_evora_s lotus_evora_s_s2 lotus_exige_240 lotus_exige_240_s3
lotus_exige_s lotus_exige_s_roadster lotus_exige_scura lotus_exige_v6_cup lotus_exos_125 lotus_exos_125_s1 mclaren_mp412c
mclaren_mp412c_gt3 mercedes_sls mercedes_sls_gt3 p4-5_2011 pagani_huayra pagani_zonda_r ruf_yellowbird shelby_cobra_427sc
tatuusfa1'''.split())
KUNOS_TRACKS = set('drift imola magione monza mugello nurburgring silverstone silverstone-international spa trento-bondone '
                   'vallelunga'.split())
SKIP_DIRS = {'__pycache__'}
SKIP_FILES = re.compile(r'(\.aclink_part|\.tmp|thumbs\.db|desktop\.ini)$', re.I)


def official(kind, cid):
    i = cid.lower()
    return i.startswith('ks_') or i in (KUNOS_CARS if kind == 'car' else KUNOS_TRACKS)


def _md5(p):
    h = hashlib.md5()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
    return h.hexdigest()


def check_files(kind, folder):
    """the files AC/AssettoServer checksum: car data.acd (or data/*.ini when unpacked), every surfaces.ini of a track"""
    out = []
    if kind == 'car':
        if os.path.isfile(os.path.join(folder, 'data.acd')): out.append('data.acd')
        elif os.path.isdir(os.path.join(folder, 'data')):
            out += ['data/' + f for f in sorted(os.listdir(os.path.join(folder, 'data'))) if f.lower().endswith('.ini')]
    else:
        for root, dirs, files in os.walk(folder):
            if 'surfaces.ini' in files and os.path.basename(root).lower() == 'data':
                out.append(os.path.relpath(os.path.join(root, 'surfaces.ini'), folder).replace('\\', '/'))
    return out


def scan(kind, folder):
    files = []
    for root, dirs, fs in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.aclink_')]
        for f in fs:
            if SKIP_FILES.search(f): continue
            p = os.path.join(root, f)
            if os.path.islink(p): continue
            files.append([os.path.relpath(p, folder).replace('\\', '/'), os.path.getsize(p)])
    return files


# ------------------------------------------------------------------------------------------------ host
class Host:
    def __init__(self):
        self.httpd, self.items, self.manifest, self.lock = None, {}, None, threading.Lock()

    def start(self, ip, port, ac, track_id, car_ids, names):
        """serve these cars and this track on ip:port (call again with the new game when re-hosting)"""
        self.stop()
        self.ac, self.manifest = ac, None
        self.items = {('track', track_id): names.get(('track', track_id), track_id)}
        for c in car_ids: self.items[('car', c)] = names.get(('car', c), c)
        host = self

        class H(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *a): pass

            def _deny(self, code=403):
                self.send_response(code); self.send_header('Content-Length', '0'); self.end_headers()

            def do_GET(self):
                try:
                    if ipaddress.ip_address(self.client_address[0]) not in TAILNET and self.client_address[0] != '127.0.0.1':
                        return self._deny()
                except ValueError:
                    return self._deny()
                u = urllib.parse.urlparse(self.path)
                q = urllib.parse.parse_qs(u.query)
                if u.path == '/manifest':
                    b = json.dumps(host.get_manifest()).encode()
                    self.send_response(200); self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)
                    return
                if u.path == '/file':
                    p = host.path(q.get('k', [''])[0], q.get('id', [''])[0], q.get('p', [''])[0])
                    if not p: return self._deny(404)
                    return self._file(p)
                self._deny(404)

            def _file(self, p):
                size = os.path.getsize(p)
                start, end = 0, size - 1
                m = re.match(r'bytes=(\d+)-(\d*)', self.headers.get('Range') or '')
                if m:
                    start = int(m.group(1)); end = int(m.group(2)) if m.group(2) else size - 1
                    if start >= size: self.send_response(416); self.send_header('Content-Range', f'bytes */{size}'); self.send_header('Content-Length', '0'); self.end_headers(); return
                    self.send_response(206); self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                else:
                    self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(end - start + 1)); self.end_headers()
                with open(p, 'rb') as f:
                    f.seek(start); left = end - start + 1
                    while left > 0:
                        b = f.read(min(1 << 20, left))
                        if not b: break
                        self.wfile.write(b); left -= len(b)

        self.httpd = ThreadingHTTPServer((ip, port), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        threading.Thread(target=self.get_manifest, daemon=True).start()     # warm up

    def stop(self):
        if self.httpd:
            self.httpd.shutdown(); self.httpd.server_close(); self.httpd = None

    def folder(self, kind, cid):
        return os.path.join(self.ac, 'content', 'cars' if kind == 'car' else 'tracks', cid)

    def get_manifest(self):
        with self.lock:
            if self.manifest is None:
                items, skipped = [], []
                for (kind, cid), name in self.items.items():
                    d = self.folder(kind, cid)
                    if official(kind, cid): skipped.append({'kind': kind, 'id': cid, 'name': name, 'why': 'official'}); continue
                    if not os.path.isdir(d): skipped.append({'kind': kind, 'id': cid, 'name': name, 'why': 'missing'}); continue
                    files = scan(kind, d)
                    items.append({'kind': kind, 'id': cid, 'name': name, 'files': files, 'total': sum(s for _, s in files),
                                  'check': {r: _md5(os.path.join(d, r)) for r in check_files(kind, d)}})
                self.manifest = {'items': items, 'skipped': skipped}
            return self.manifest

    def path(self, kind, cid, rel):
        if (kind, cid) not in self.items or official(kind, cid) or not rel: return None
        base = os.path.realpath(self.folder(kind, cid))
        p = os.path.realpath(os.path.join(base, rel))
        if not p.startswith(base + os.sep) or not os.path.isfile(p) or SKIP_FILES.search(p): return None
        return p


HOST = Host()


# ------------------------------------------------------------------------------------------------ guest
def manifest(ip, port, timeout=8):
    with urllib.request.urlopen(f'http://{ip}:{port}/manifest', timeout=timeout) as r:
        return json.loads(r.read())


def local_folder(ac, kind, cid):
    return os.path.join(ac, 'content', 'cars' if kind == 'car' else 'tracks', cid)


def compare(ac, man):
    """each manifest item gets 'state': ok / missing / different, and 'need' (bytes to download)"""
    for it in man['items']:
        d = local_folder(ac, it['kind'], it['id'])
        if not os.path.isdir(d):
            it['state'], it['need'] = 'missing', it['total']; continue
        diff = [r for r, h in it['check'].items() if not os.path.isfile(os.path.join(d, r)) or _md5(os.path.join(d, r)) != h]
        it['state'] = 'different' if diff else 'ok'
        it['need'] = sum(s for r, s in todo(d, it)) if diff else 0
    return man


def todo(d, it):
    """files to fetch into an existing folder: missing, different size, or a checksummed file"""
    out = []
    for r, s in it['files']:
        p = os.path.join(d, r)
        if not os.path.isfile(p) or os.path.getsize(p) != s or r in it['check']: out.append((r, s))
    return out


class Fetch:
    def __init__(self):
        self.state = {'running': False}
        self.cancel = False

    def status(self):
        return dict(self.state)

    def start(self, ac, ip, port, ids=None):
        if self.state.get('running'): return
        self.cancel = False
        self.state = {'running': True, 'done': 0, 'total': 0, 'item': '', 'error': None, 'speed': 0, 'finished': False}
        threading.Thread(target=self._run, args=(ac, ip, port, ids), daemon=True).start()

    def _run(self, ac, ip, port, ids):
        st = self.state
        try:
            man = compare(ac, manifest(ip, port))
            items = [it for it in man['items'] if it['state'] != 'ok' and (ids is None or f"{it['kind']}:{it['id']}" in ids)]
            plan = []
            for it in items:
                final = local_folder(ac, it['kind'], it['id'])
                new = it['state'] == 'missing'
                dest = os.path.join(os.path.dirname(final), '.aclink_' + it['id']) if new else final
                plan.append((it, final, dest, new, it['files'] if new else todo(final, it)))
            st['total'] = sum(s for p in plan for _, s in p[4])
            t0, done0 = time.time(), 0
            for it, final, dest, new, files in plan:
                st['item'] = it['name']
                for rel, size in files:
                    if self.cancel: raise InterruptedError
                    p = os.path.join(dest, *rel.split('/'))
                    if os.path.isfile(p) and os.path.getsize(p) == size and new and rel not in it['check']:
                        st['done'] += size; continue                       # already fetched in an earlier try
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    part = p + '.aclink_part'
                    have = os.path.getsize(part) if os.path.isfile(part) else 0
                    if have > size: os.remove(part); have = 0
                    st['done'] += have
                    if have < size or size == 0:
                        q = urllib.parse.urlencode({'k': it['kind'], 'id': it['id'], 'p': rel})
                        req = urllib.request.Request(f'http://{ip}:{port}/file?{q}', headers={'Range': f'bytes={have}-'} if have else {})
                        with urllib.request.urlopen(req, timeout=30) as r, open(part, 'ab' if have and r.status == 206 else 'wb') as f:
                            if have and r.status != 206: st['done'] -= have
                            while True:
                                if self.cancel: raise InterruptedError
                                b = r.read(1 << 20)
                                if not b: break
                                f.write(b); st['done'] += len(b)
                                dt = time.time() - t0
                                if dt > 1: st['speed'] = (st['done'] - done0) / dt
                    os.replace(part, p)
                if new:
                    if os.path.isdir(final): shutil.rmtree(dest)  # appeared meanwhile; keep what is there
                    else: os.replace(dest, final)
            st['finished'] = True
        except InterruptedError:
            st['error'] = 'Cancelled. Start again to resume where it stopped.'
        except Exception as e:
            st['error'] = f'Download stopped: {e}. Start again to resume.'
        finally:
            st['running'] = False


FETCH = Fetch()
