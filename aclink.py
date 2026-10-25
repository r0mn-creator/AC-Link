"""AC Link: host or join an Assetto Corsa game with a friend over Tailscale.

The host picks a track, conditions and allowed cars and clicks Host: this writes an AssettoServer preset, copies the
few content files the server needs (track surfaces and traffic spline, car data.acd), starts AssettoServer and shows
an 8-character code. The code is the host's Tailscale address (100.64.0.0/10, 22 bits) plus the server's HTTP port.
The guest types the code, sees the server's track and cars, and joins through Content Manager.

The UI is a local web page (ui/) opened in an Edge app window; this file is its backend on 127.0.0.1:UI_PORT."""
import json, os, re, shutil, subprocess, sys, threading, time, urllib.parse, urllib.request, winreg
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

import share

APP = 'AC Link'
UI_PORT = 47600
ROOT = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
UI_DIR = os.path.join(getattr(sys, '_MEIPASS', ROOT), 'ui')
SRV = os.path.join(ROOT, 'vendor', 'assettoserver')
PRESET = 'aclink'
SETTINGS = os.path.join(ROOT, 'settings.json')
LOG = os.path.join(ROOT, 'server.log')
NOWIN = 0x08000000                                     # CREATE_NO_WINDOW

# CSP WeatherFX types (server_cfg GRAPHICS=..._type=N)
WEATHERS = [
    ('clear', 'Clear', 15, 26), ('few_clouds', 'Few clouds', 16, 25), ('scattered', 'Partly cloudy', 17, 24),
    ('overcast', 'Overcast', 19, 21), ('mist', 'Mist', 21, 18), ('fog', 'Fog', 20, 16),
    ('light_drizzle', 'Light drizzle', 3, 19), ('light_rain', 'Light rain', 6, 18), ('rain', 'Rain', 7, 17),
    ('heavy_rain', 'Heavy rain', 8, 16), ('thunderstorm', 'Thunderstorm', 1, 20),
    ('light_snow', 'Light snow', 9, -2), ('snow', 'Snow', 10, -4),
]
DENSITY = {'low': 0.5, 'normal': 1.0, 'high': 1.5}

DEFAULTS = {
    'ac_path': '',
    'server_name': '',
    'host': {'track': '', 'layout': '', 'hour': 14.0, 'weather': 'clear', 'traffic': True, 'density': 'normal',
             'cars': [], 'max_players': 4},
    'ports': {'tcp': 9600, 'http': 8081},
    'traffic_cars': [], 'traffic_count': 30,
    'server': {},          # server_cfg [SERVER] overrides
    'sessions': {'practice_minutes': 600},
    'extra': {},           # extra_cfg.yml overrides (only changed keys)
    'plugins': {},         # name -> {'enabled': bool, 'cfg': {...overrides}}
    'csp_extra': '',       # raw csp_extra_options.ini
    'recent_codes': [],
}

# Kunos server_cfg keys exposed in Settings > Server rules: key -> (label, default, kind, help)
SERVER_KEYS = {
    'PASSWORD': ('Password', '', 'str', 'Players need this to join. Empty = no password (Tailscale already keeps it private).'),
    'ADMIN_PASSWORD': ('Admin password', '', 'str', 'Type /admin <password> in chat to use admin commands. Empty = random.'),
    'ABS_ALLOWED': ('ABS', 1, ('0:Off', '1:Car default', '2:Forced on'), ''),
    'TC_ALLOWED': ('Traction control', 1, ('0:Off', '1:Car default', '2:Forced on'), ''),
    'STABILITY_ALLOWED': ('Stability control allowed', 1, 'bool', ''),
    'AUTOCLUTCH_ALLOWED': ('Auto clutch allowed', 1, 'bool', ''),
    'TYRE_BLANKETS_ALLOWED': ('Tyre blankets', 1, 'bool', 'Tyres start warm.'),
    'FORCE_VIRTUAL_MIRROR': ('Force virtual mirror', 0, 'bool', ''),
    'DAMAGE_MULTIPLIER': ('Damage %', 100, 'int', ''),
    'FUEL_RATE': ('Fuel use %', 100, 'int', ''),
    'TYRE_WEAR_RATE': ('Tyre wear %', 100, 'int', ''),
    'ALLOWED_TYRES_OUT': ('Tyres allowed off track', -1, 'int', '-1 = no limit (free roam).'),
    'TIME_OF_DAY_MULT': ('Time speed', 1, 'int', '1 = real time, 2 = twice as fast...'),
    'CLIENT_SEND_INTERVAL_HZ': ('Network update rate (Hz)', 18, 'int', ''),
    'PICKUP_MODE_ENABLED': ('Pickup mode', 1, 'bool', 'Players can join any time.'),
    'LOOP_MODE': ('Loop sessions', 1, 'bool', ''),
    'LEGAL_TYRES': ('Legal tyres', '', 'str', 'Tyre short names, e.g. SV;V;H. Empty = all.'),
}

# basic-mode choices that differ from AssettoServer's own defaults (friends over Tailscale, free roam)
EXTRA_BASE = {
    'UseSteamAuth': False,
    'EnableWeatherFx': True,
    'EnableServerDetails': True,
    'IgnoreConfigurationErrors': {'MissingCarChecksums': True, 'MissingTrackParams': True},
}


# ---------------------------------------------------------------------------------------------------- settings
def load_settings():
    s = json.loads(json.dumps(DEFAULTS))
    try:
        with open(SETTINGS, encoding='utf-8') as f:
            got = json.load(f)
        for k, v in got.items():
            if isinstance(v, dict) and isinstance(s.get(k), dict): s[k].update(v)
            else: s[k] = v
    except (OSError, ValueError):
        pass
    return s


def save_settings(s):
    tmp = SETTINGS + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f: json.dump(s, f, indent=1)
    os.replace(tmp, SETTINGS)


# ---------------------------------------------------------------------------------------------------- finding things
def steam_libraries():
    out = []
    for hive, key in ((winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam'),
                      (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam')):
        try:
            with winreg.OpenKey(hive, key) as k:
                for name in ('SteamPath', 'InstallPath'):
                    try: out.append(winreg.QueryValueEx(k, name)[0])
                    except OSError: pass
        except OSError:
            pass
    libs = []
    for st in out:
        libs.append(st)
        try:
            txt = open(os.path.join(st, 'steamapps', 'libraryfolders.vdf'), encoding='utf-8', errors='ignore').read()
            libs += [p.replace('\\\\', '\\') for p in re.findall(r'"path"\s+"([^"]+)"', txt)]
        except OSError:
            pass
    return list(dict.fromkeys(os.path.normpath(p) for p in libs))


def find_ac(s):
    if s.get('ac_path') and os.path.isfile(os.path.join(s['ac_path'], 'acs.exe')): return s['ac_path']
    for lib in steam_libraries():
        p = os.path.join(lib, 'steamapps', 'common', 'assettocorsa')
        if os.path.isfile(os.path.join(p, 'acs.exe')): return p
    return ''


def tailscale_exe():
    p = shutil.which('tailscale')
    if p: return p
    p = os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'), 'Tailscale', 'tailscale.exe')
    return p if os.path.isfile(p) else None


def tailscale():
    """{'installed', 'running', 'ip', 'name'}"""
    exe = tailscale_exe()
    if not exe: return {'installed': False, 'running': False, 'ip': None, 'name': None}
    try:
        r = subprocess.run([exe, 'status', '--json'], capture_output=True, timeout=6, creationflags=NOWIN)
        st = json.loads(r.stdout.decode('utf-8', 'ignore') or '{}')
        me = st.get('Self') or {}
        ips = [a for a in (me.get('TailscaleIPs') or []) if '.' in a]
        ok = st.get('BackendState') == 'Running' and bool(ips)
        return {'installed': True, 'running': ok, 'ip': ips[0] if ok else None,
                'name': (me.get('DNSName') or '').split('.')[0] or me.get('HostName')}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'installed': True, 'running': False, 'ip': None, 'name': None}


_fw = {'t': 0, 'v': None}


def firewall():
    """'blocked' if Windows Firewall has an inbound Block rule for our AssettoServer.exe on the Private profile (the
    Tailscale adapter is Private), 'allowed', or 'unknown' (no rule yet: Windows asks the first time it listens)"""
    if time.time() - _fw['t'] < 60 and _fw['v']: return _fw['v']
    progs = "','".join(p.lower().replace("'", "''") for p in fw_programs())
    ps = ("$ErrorActionPreference='SilentlyContinue'; $p=@('PROGS'); Get-NetFirewallApplicationFilter | "
          "? { $p -contains $_.Program.ToLower() } | "
          "ForEach-Object { $r = $_ | Get-NetFirewallRule; if ($r.Enabled -eq 'True' -and $r.Direction -eq 'Inbound') "
          "{ '' + $r.Action + '|' + ($r.Profile -replace ' ','') } }").replace('PROGS', progs)
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, timeout=40, creationflags=NOWIN)
        rules = [l.split('|') for l in r.stdout.decode('utf-8', 'ignore').split() if '|' in l]
    except (OSError, subprocess.SubprocessError):
        rules = []
    priv = [a for a, prof in rules if 'Private' in prof or prof == 'Any']
    v = 'blocked' if 'Block' in priv else 'allowed' if 'Allow' in priv else 'unknown'
    _fw.update(t=time.time(), v=v)
    return v


def fw_programs():
    """AssettoServer, and AC Link itself when built (it serves shared content); in development python.exe is left alone"""
    return [os.path.join(SRV, 'AssettoServer.exe')] + ([sys.executable] if getattr(sys, 'frozen', False) else [])


def firewall_fix():
    s = STATE['settings']
    cmds = []
    for i, exe in enumerate(fw_programs()):
        cmds.append("Get-NetFirewallApplicationFilter | ? { $_.Program -eq 'EXE' } | Get-NetFirewallRule | "
                    "Remove-NetFirewallRule".replace('EXE', exe))
        name, port = (('AssettoServer (AC Link)', '') if i == 0 else
                      ('AC Link content sharing', f" -Protocol TCP -LocalPort {s['ports']['http'] + 1}"))
        cmds.append(f"New-NetFirewallRule -DisplayName '{name}' -Direction Inbound -Action Allow -Profile Private{port} "
                    f"-Program '{exe}'")
    return '; '.join(cmds)


# ---------------------------------------------------------------------------------------------------- first-run setup
AS_TAG = 'v0.0.55-pre45'                                # the AssettoServer build AC Link is tested with
AS_URL = f'https://github.com/compujuckel/AssettoServer/releases/download/{AS_TAG}/assetto-server-win-x64.zip'
LINKS = {
    'ac': 'https://store.steampowered.com/app/244210/Assetto_Corsa/',
    'cm': 'https://acstuff.club/app/',
    'csp': 'https://acstuff.club/patch/',
    'tailscale': 'https://tailscale.com/download',
    'tailscale_share': 'https://tailscale.com/kb/1084/sharing',
    'tailscale_machines': 'https://login.tailscale.com/admin/machines',
    'tailscale_acl': 'https://login.tailscale.com/admin/acls',
    'assettoserver': 'https://github.com/compujuckel/AssettoServer',
    'webview2': 'https://developer.microsoft.com/microsoft-edge/webview2/',
}
INSTALL = {'running': False, 'done': 0, 'total': 0, 'error': None, 'stage': ''}


def has_server():
    return os.path.isfile(os.path.join(SRV, 'AssettoServer.exe'))


def has_cm():
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r'acmanager\shell\open\command'): return True
    except OSError:
        return False


def has_csp(ac):
    return bool(ac) and os.path.isfile(os.path.join(ac, 'dwrite.dll')) and os.path.isdir(os.path.join(ac, 'extension'))


def setup_status():
    ac = find_ac(STATE['settings'])
    ts = tailscale()
    return {'server': has_server(), 'server_tag': AS_TAG, 'ac': ac, 'cm': has_cm(), 'csp': has_csp(ac),
            'tailscale': ts, 'install': dict(INSTALL), 'links': LINKS}


def install_server():
    """download AssettoServer from its official GitHub release into vendor/assettoserver, then let it write its
    config schemas (-g), which the advanced settings are built from"""
    INSTALL.update(running=True, done=0, total=0, error=None, stage='Downloading AssettoServer')
    try:
        os.makedirs(SRV, exist_ok=True)
        zp = os.path.join(ROOT, 'vendor', f'assetto-server-{AS_TAG}.zip')
        with urllib.request.urlopen(urllib.request.Request(AS_URL, headers={'User-Agent': APP}), timeout=60) as r, \
                open(zp + '.part', 'wb') as f:
            INSTALL['total'] = int(r.headers.get('Content-Length') or 0)
            while True:
                b = r.read(1 << 20)
                if not b: break
                f.write(b); INSTALL['done'] += len(b)
        os.replace(zp + '.part', zp)
        INSTALL['stage'] = 'Unpacking'
        import zipfile
        with zipfile.ZipFile(zp) as z:
            for m in z.infolist():                     # stay inside the target folder
                dest = os.path.realpath(os.path.join(SRV, m.filename))
                if not dest.startswith(os.path.realpath(SRV) + os.sep) and dest != os.path.realpath(SRV): continue
                z.extract(m, SRV)
        os.remove(zp)
        INSTALL['stage'] = 'Preparing settings'
        probe = os.path.join(SRV, 'presets', 'schema_probe')
        os.makedirs(probe, exist_ok=True)
        with open(os.path.join(probe, 'server_cfg.ini'), 'w') as f:
            f.write('[SERVER]\nNAME=probe\nCARS=x\nTRACK=x\nMAX_CLIENTS=1\nUDP_PORT=9600\nTCP_PORT=9600\nHTTP_PORT=8081\nREGISTER_TO_LOBBY=0\n')
        try:
            subprocess.run([os.path.join(SRV, 'AssettoServer.exe'), '-p', 'schema_probe', '-g'], cwd=SRV, timeout=60,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NOWIN)
        except subprocess.TimeoutExpired:
            pass
        shutil.rmtree(probe, ignore_errors=True)
        INSTALL['stage'] = 'Done'
    except Exception as e:
        INSTALL['error'] = f"Couldn't download AssettoServer: {e}"
    finally:
        INSTALL['running'] = False


# ---------------------------------------------------------------------------------------------------- join code
ALPHA = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'                # Crockford base32: no I, L, O, U


def make_code(ip, http_port):
    a = [int(x) for x in ip.split('.')]
    if a[0] != 100 or not 64 <= a[1] < 128: return None  # not a Tailscale address
    host = ((a[1] - 64) << 16) | (a[2] << 8) | a[3]       # 22 bits
    n = (host << 16) | (http_port & 0xffff)               # 38 bits; top 2 bits = version 0
    s = ''.join(ALPHA[(n >> (5 * i)) & 31] for i in reversed(range(8)))
    return s[:4] + '-' + s[4:]


def read_code(code):
    c = re.sub(r'[\s\-]', '', code.upper()).replace('O', '0').replace('I', '1').replace('L', '1')
    m = re.fullmatch(r'(\d{1,3}(?:\.\d{1,3}){3})(?::(\d+))?', code.strip())
    if m: return m.group(1), int(m.group(2) or 8081)      # a plain address works too
    if len(c) != 8 or any(ch not in ALPHA for ch in c): return None
    n = 0
    for ch in c: n = n * 32 + ALPHA.index(ch)
    if n >> 38: return None
    host, port = n >> 16, n & 0xffff
    return f'100.{64 + (host >> 16)}.{(host >> 8) & 255}.{host & 255}', port


# ---------------------------------------------------------------------------------------------------- content
def _ui_json(path):
    try:
        t = open(path, 'rb').read().decode('utf-8-sig', 'ignore')
    except OSError:
        return {}
    try:
        return json.loads(t, strict=False)
    except ValueError:
        d = {}
        for k in ('name', 'brand', 'class', 'pitboxes', 'description'):
            m = re.search(r'"%s"\s*:\s*"([^"]*)"' % k, t)
            if m: d[k] = m.group(1)
        return d


_cache = {}


def tracks(ac):
    if ('tracks', ac) in _cache: return _cache[('tracks', ac)]
    out, base = [], os.path.join(ac, 'content', 'tracks')
    for tid in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        td = os.path.join(base, tid)
        if not os.path.isdir(td) or tid.lower() in ('csp',): continue
        layouts = []
        uid = os.path.join(td, 'ui')
        subs = [d for d in os.listdir(uid) if os.path.isfile(os.path.join(uid, d, 'ui_track.json'))] if os.path.isdir(uid) else []
        if os.path.isfile(os.path.join(uid, 'ui_track.json')): layouts.append('')
        layouts += [d for d in subs if os.path.isdir(os.path.join(td, d))]
        for lay in layouts:
            ui = os.path.join(uid, lay) if lay else uid
            j = _ui_json(os.path.join(ui, 'ui_track.json'))
            ld = os.path.join(td, lay) if lay else td
            try: pits = int(re.sub(r'\D', '', str(j.get('pitboxes', ''))) or 0)
            except ValueError: pits = 0
            out.append({
                'id': tid, 'layout': lay, 'name': (j.get('name') or tid).strip(), 'pits': pits,
                'traffic': os.path.isfile(os.path.join(ld, 'ai', 'fast_lane.aip')),
                'preview': os.path.join(ui, 'preview.png'), 'outline': os.path.join(ui, 'outline.png'),
                'country': j.get('country') or '', 'length': j.get('length') or '',
            })
    _cache[('tracks', ac)] = out
    return out


def cars(ac):
    if ('cars', ac) in _cache: return _cache[('cars', ac)]
    out, base = [], os.path.join(ac, 'content', 'cars')
    for cid in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        cd = os.path.join(base, cid)
        if not os.path.isdir(cd): continue
        j = _ui_json(os.path.join(cd, 'ui', 'ui_car.json'))
        sk = os.path.join(cd, 'skins')
        skins = sorted(d for d in os.listdir(sk) if os.path.isdir(os.path.join(sk, d))) if os.path.isdir(sk) else []
        if not skins and not j: continue
        out.append({
            'id': cid, 'name': (j.get('name') or cid).strip(), 'brand': (j.get('brand') or '').strip(),
            'class': (j.get('class') or '').strip(), 'skins': skins,
            'preview': os.path.join(sk, skins[0], 'preview.jpg') if skins else '',
            'traffic': 'traffic' in cid.lower(),
        })
    _cache[('cars', ac)] = out
    return out


# ---------------------------------------------------------------------------------------------------- schemas
def schema(name='extra_cfg'):
    try:
        with open(os.path.join(SRV, 'cfg', 'schemas', f'{name}.schema.json'), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def plugin_list():
    """[{name: 'RandomWeatherPlugin', cfg: 'plugin_random_weather_cfg', schema: bool}]"""
    out, pd = [], os.path.join(SRV, 'plugins')
    for name in sorted(os.listdir(pd)) if os.path.isdir(pd) else []:
        if name in ('SamplePlugin',): continue
        stem = 'plugin_' + re.sub(r'(?<!^)(?=[A-Z])', '_', name.replace('Plugin', '')).lower() + '_cfg'
        out.append({'name': name, 'cfg': stem,
                    'schema': os.path.isfile(os.path.join(SRV, 'cfg', 'schemas', stem + '.schema.json'))})
    return out


def merge(a, b):
    out = dict(a)
    for k, v in (b or {}).items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


# ---------------------------------------------------------------------------------------------------- server
class Server:
    def __init__(self):
        self.proc, self.started, self.info, self.error, self.log_f = None, 0, None, None, None
        self.summary, self.warnings = None, []

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.running():
            self.proc.terminate()
            try: self.proc.wait(8)
            except subprocess.TimeoutExpired: self.proc.kill()
        self.proc = None
        if self.log_f: self.log_f.close(); self.log_f = None

    def start(self):
        self.stop()
        self.log_f = open(LOG, 'w', encoding='utf-8', errors='replace')
        self.proc = subprocess.Popen([os.path.join(SRV, 'AssettoServer.exe'), '-p', PRESET], cwd=SRV,
                                     stdout=self.log_f, stderr=subprocess.STDOUT, creationflags=NOWIN)
        self.started, self.error = time.time(), None


SERVER = Server()


def _copy(src, dst):
    if not os.path.isfile(src): return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if not os.path.isfile(dst) or os.path.getsize(dst) != os.path.getsize(src) or open(dst, 'rb').read() != open(src, 'rb').read():
        shutil.copyfile(src, dst)
    return True


def traffic_pack(allc):
    """default traffic: the biggest installed pack (cars grouped by the part of the folder name before 'traffic',
    or 'traffic_<maker>' for packs that start with it)"""
    groups = {}
    for c in allc.values():
        if not c['traffic']: continue
        i = c['id'].lower()
        key = i.split('traffic')[0] or 'traffic_' + (i.split('_') + [''])[1]
        groups.setdefault(key, []).append(c['id'])
    return max(groups.values(), key=len) if groups else []


def sun_angle(hour):
    return round(16 * (hour * 3600 - 46800) / 3600, 2)


def build(s, ac):
    """write the preset and content the server needs; returns (summary, warnings)"""
    h, warn = s['host'], []
    tr = next((t for t in tracks(ac) if t['id'] == h['track'] and t['layout'] == h['layout']), None)
    if not tr: raise ValueError('Pick a track first.')
    allc = {c['id']: c for c in cars(ac)}
    picked = [c for c in h['cars'] if c in allc]
    if not picked: raise ValueError('Pick at least one car your friends can drive.')
    traffic = bool(h['traffic'] and tr['traffic'])
    if h['traffic'] and not tr['traffic']: warn.append(f"{tr['name']} has no traffic spline (ai/fast_lane.aip), so traffic is off.")

    # --- entry list: each allowed car gets max_players slots; traffic cars after
    pits = tr['pits'] or 64
    mp = max(1, int(h['max_players']))
    per = mp
    tcars = [c for c in (s['traffic_cars'] or traffic_pack(allc)) if c in allc] if traffic else []
    tcount = min(int(s['traffic_count']), 200) if tcars else 0
    while per > 1 and len(picked) * per + tcount > pits: per -= 1
    if len(picked) * per + tcount > pits:
        tcount = max(0, pits - len(picked) * per)
        warn.append(f"{tr['name']} has {pits} pit spots, so traffic is limited to {tcount} cars.")
    if per < mp: warn.append(f'{len(picked)} cars x {mp} players does not fit the {pits} pit spots: {per} of each car.')
    ents = []
    for c in picked:
        sk = allc[c]['skins'] or ['']
        ents += [('none', c, sk[i % len(sk)]) for i in range(per)]
    for i in range(tcount):
        c = tcars[i % len(tcars)]; sk = allc[c]['skins'] or ['']
        ents.append(('fixed', c, sk[(i // len(tcars)) % len(sk)]))
    el = []
    for i, (ai, model, skin) in enumerate(ents):
        el.append(f'[CAR_{i}]\nMODEL={model}\nSKIN={skin}\nSPECTATOR_MODE=0\nDRIVERNAME=\nTEAM=\nGUID=\nBALLAST=0\nRESTRICTOR=0\nAI={ai}\n')

    # --- server_cfg.ini
    w = next((x for x in WEATHERS if x[0] == h['weather']), WEATHERS[0])
    name = s['server_name'] or f"{os.environ.get('USERNAME', 'AC')}'s game"
    srv = {
        'NAME': name, 'CARS': ';'.join(dict.fromkeys([e[1] for e in ents])), 'CONFIG_TRACK': tr['layout'],
        'TRACK': tr['id'], 'SUN_ANGLE': sun_angle(float(h['hour'])), 'PASSWORD': '', 'ADMIN_PASSWORD': '',
        'UDP_PORT': s['ports']['tcp'], 'TCP_PORT': s['ports']['tcp'], 'HTTP_PORT': s['ports']['http'],
        'MAX_BALLAST_KG': 0, 'QUALIFY_MAX_WAIT_PERC': 120, 'RACE_PIT_WINDOW_START': 0, 'RACE_PIT_WINDOW_END': 0,
        'REVERSED_GRID_RACE_POSITIONS': 0, 'LOCKED_ENTRY_LIST': 0, 'START_RULE': 0, 'RACE_GAS_PENALTY_DISABLED': 0,
        'RESULT_SCREEN_TIME': 60, 'MAX_CONTACTS_PER_KM': -1, 'RACE_EXTRA_LAP': 0, 'UDP_PLUGIN_LOCAL_PORT': 0,
        'UDP_PLUGIN_ADDRESS': '', 'AUTH_PLUGIN_ADDRESS': '', 'KICK_QUORUM': 85, 'VOTING_QUORUM': 80,
        'VOTE_DURATION': 20, 'BLACKLIST_MODE': 1, 'SLEEP_TIME': 1, 'RACE_OVER_TIME': 180,
        'REGISTER_TO_LOBBY': 0, 'MAX_CLIENTS': len(ents), 'NUM_THREADS': 2, 'WELCOME_MESSAGE': '',
    }
    for k, (_, d, _, _) in SERVER_KEYS.items(): srv[k] = d
    srv.update({k: v for k, v in s['server'].items() if k in SERVER_KEYS or k not in srv})
    if not srv['ADMIN_PASSWORD']: srv['ADMIN_PASSWORD'] = os.urandom(6).hex()
    ini = '[SERVER]\n' + ''.join(f'{k}={v}\n' for k, v in srv.items())
    ini += f"\n[PRACTICE]\nNAME=Free roam\nTIME={int(s['sessions'].get('practice_minutes', 600))}\nIS_OPEN=1\nINFINITE=1\n"
    ini += '\n[DYNAMIC_TRACK]\nSESSION_START=100\nRANDOMNESS=0\nSESSION_TRANSFER=100\nLAP_GAIN=1\n'
    ini += (f'\n[WEATHER_0]\nGRAPHICS=3_clear_type={w[2]}\nBASE_TEMPERATURE_AMBIENT={w[3]}\n'
            f'BASE_TEMPERATURE_ROAD={4 if w[3] > 20 else 1}\nVARIATION_AMBIENT=1\nVARIATION_ROAD=1\n'
            'WIND_BASE_SPEED_MIN=0\nWIND_BASE_SPEED_MAX=8\nWIND_BASE_DIRECTION=0\nWIND_VARIATION_DIRECTION=180\n')

    # --- extra_cfg.yml
    ext = merge(EXTRA_BASE, {'EnableAi': traffic, 'ServerDescription': f"AC Link - {tr['name']}"})
    if traffic: ext['AiParams'] = {'TrafficDensity': DENSITY.get(h['density'], 1.0), 'TwoWayTraffic': True}
    ext = merge(ext, s['extra'])
    ext['EnableAi'] = traffic and ext.get('EnableAi', True)
    pl = [p for p in plugin_list() if s['plugins'].get(p['name'], {}).get('enabled')]
    ext['EnablePlugins'] = [p['name'] for p in pl] or None

    # --- write
    pdir = os.path.join(SRV, 'presets', PRESET)
    os.makedirs(pdir, exist_ok=True)
    files = {'server_cfg.ini': ini, 'entry_list.ini': '\n'.join(el),
             'extra_cfg.yml': yaml.safe_dump(ext, sort_keys=False, allow_unicode=True)}
    for p in pl:
        try:
            with open(os.path.join(SRV, 'cfg', 'reference', p['cfg'] + '.reference.yml'), encoding='utf-8') as f: ref = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            ref = {}
        files[p['cfg'] + '.yml'] = yaml.safe_dump(merge(ref, s['plugins'][p['name']].get('cfg') or {}), sort_keys=False)
    if s.get('csp_extra', '').strip(): files['csp_extra_options.ini'] = s['csp_extra']
    elif os.path.isfile(os.path.join(pdir, 'csp_extra_options.ini')): os.remove(os.path.join(pdir, 'csp_extra_options.ini'))
    for fn, txt in files.items():
        with open(os.path.join(pdir, fn), 'w', encoding='utf-8') as f: f.write(txt)

    # --- content: only what the server reads (checksums, traffic spline)
    src_t = os.path.join(ac, 'content', 'tracks', tr['id'])
    dst_t = os.path.join(SRV, 'content', 'tracks', tr['id'])
    lay = tr['layout']
    _copy(os.path.join(src_t, lay, 'data', 'surfaces.ini'), os.path.join(dst_t, lay, 'data', 'surfaces.ini'))
    if traffic: _copy(os.path.join(src_t, lay, 'ai', 'fast_lane.aip'), os.path.join(dst_t, lay, 'ai', 'fast_lane.aip'))
    for c in dict.fromkeys(e[1] for e in ents):
        _copy(os.path.join(ac, 'content', 'cars', c, 'data.acd'), os.path.join(SRV, 'content', 'cars', c, 'data.acd'))
    return {'track': tr['name'], 'players': len(picked) * per, 'traffic': tcount, 'entries': len(ents),
            'car_ids': list(dict.fromkeys(e[1] for e in ents)),
            'weather': w[1], 'files': files}, warn


def http_get(url, timeout=4):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'ignore'))


def log_tail(n=200):
    try:
        with open(LOG, encoding='utf-8', errors='replace') as f: lines = f.readlines()
    except OSError:
        return []
    return [re.sub(r'\x1b\[[0-9;]*m', '', l.rstrip()) for l in lines[-n:]]


def server_state(s):
    st = {'running': SERVER.running(), 'ready': False, 'info': None, 'error': None}
    if st['running']:
        try:
            st['info'] = http_get(f"http://127.0.0.1:{s['ports']['http']}/INFO", 1.5)
            st['ready'] = True
        except Exception:
            pass
    elif SERVER.started:
        tail = [l for l in log_tail(150) if l.strip() and not l.lstrip().startswith('at ') and not set(l.strip()) <= set('═�')]
        msg = next((l for l in reversed(tail) if re.search(r'(Configuration|Startup)Exception: ', l)), None)
        if msg: msg = msg.split('Exception: ', 1)[1]
        else:
            i = next((i for i, l in enumerate(tail) if 'has failed to start' in l), len(tail))
            errs = [l for l in tail[:i] if re.search(r'\b(ERR|FTL)\b|Exception', l)]
            msg = tail[i - 1] if 0 < i < len(tail) else (errs[-1] if errs else 'The server stopped.')
        st['error'] = msg[:400]
    return st


# ---------------------------------------------------------------------------------------------------- HTTP API
STATE = {'settings': load_settings(), 'last_ping': time.time()}


def api(path, q, body):
    s = STATE['settings']
    ac = find_ac(s)
    if path == '/api/ping':
        STATE['last_ping'] = time.time(); return {'ok': True}
    if path == '/api/status':
        ts = tailscale()
        st = server_state(s)
        code = make_code(ts['ip'], s['ports']['http']) if ts['ip'] else None
        st['summary'], st['warnings'] = SERVER.summary, SERVER.warnings
        return {'app': APP, 'ac_path': ac, 'tailscale': ts, 'server': st, 'code': code, 'firewall': _fw['v'],
                'sharing': bool(share.HOST.httpd), 'share_port': s['ports']['http'] + 1,
                'address': f"{ts['ip']}:{s['ports']['http']}" if ts['ip'] else None}
    if path == '/api/settings':
        if body is not None:
            for k, v in body.items():
                if k in DEFAULTS: s[k] = v
            save_settings(s)
            _cache.clear() if 'ac_path' in body else None
        return {'settings': s, 'weathers': [{'id': w[0], 'name': w[1]} for w in WEATHERS],
                'server_keys': {k: {'label': v[0], 'default': v[1], 'kind': v[2], 'help': v[3]} for k, v in SERVER_KEYS.items()},
                'extra_base': EXTRA_BASE}
    if path == '/api/content':
        if not ac: return {'error': 'Assetto Corsa was not found. Set its folder in Settings.'}
        strip = lambda d: {k: v for k, v in d.items() if k not in ('preview', 'outline')}
        return {'tracks': [strip(t) for t in tracks(ac)], 'cars': [strip(c) for c in cars(ac)]}
    if path == '/api/schemas':
        return {'extra': schema('extra_cfg'), 'plugins': [dict(p, schema=schema(p['cfg'])) for p in plugin_list()]}
    if path == '/api/host':
        if body: s['host'].update(body); save_settings(s)
        if not ac: return {'error': 'Assetto Corsa was not found. Set its folder in Settings.'}
        if not has_server(): return {'error': 'AssettoServer is not installed yet. Open Settings > General > Check setup.'}
        try:
            summary, warn = build(s, ac)
        except ValueError as e:
            return {'error': str(e)}
        summary.pop('files')
        summary['hour'] = s['host']['hour']
        _fw['t'] = 0; threading.Thread(target=lambda: (time.sleep(6), firewall()), daemon=True).start()
        SERVER.start()
        ids = summary.pop('car_ids')
        ts = tailscale()
        if ts['ip']:
            allc = {c['id']: c['name'] for c in cars(ac)}
            names = {('car', c): allc.get(c, c) for c in ids}
            names[('track', s['host']['track'])] = summary['track']
            try:
                share.HOST.start(ts['ip'], s['ports']['http'] + 1, ac, s['host']['track'], ids, names)
            except OSError as e:
                warn.append(f"Content sharing couldn't start on port {s['ports']['http'] + 1}: {e}")
        SERVER.summary, SERVER.warnings = summary, warn
        return {'ok': True, 'summary': summary, 'warnings': warn}
    if path == '/api/preview':
        try:
            summary, warn = build(s, ac) if ac else ({}, [])
            return {'files': summary.get('files', {}), 'warnings': warn}
        except ValueError as e:
            return {'error': str(e)}
    if path == '/api/fixfw':                             # the user clicked Fix: elevated PowerShell, Windows asks first
        ps1 = os.path.join(os.environ.get('TEMP', ROOT), 'aclink_firewall.ps1')
        with open(ps1, 'w', encoding='utf-8-sig') as f: f.write(firewall_fix() + '\n')
        try:
            subprocess.run(['powershell', '-NoProfile', '-Command',
                            "Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "
                            f"'-NoProfile','-ExecutionPolicy','Bypass','-File','\"{ps1}\"'"],
                           timeout=180, creationflags=NOWIN)
        except (OSError, subprocess.SubprocessError):
            pass
        _fw['t'] = 0
        return {'firewall': firewall()}
    if path == '/api/setup':
        return setup_status()
    if path == '/api/install_server':
        if not INSTALL['running'] and not has_server():
            threading.Thread(target=install_server, daemon=True).start()
            INSTALL['running'] = True
        return dict(INSTALL)
    if path == '/api/link':                              # open one of the known download pages in the browser
        u = LINKS.get((body or {}).get('id'))
        if u: os.startfile(u)
        return {'ok': bool(u)}
    if path == '/api/stop':
        SERVER.stop(); share.HOST.stop(); SERVER.started = 0; return {'ok': True}
    if path == '/api/fetch':
        addr = read_code((body or {}).get('code', ''))
        if not addr or not ac: return {'error': 'Bad code.' if ac else 'Assetto Corsa was not found.'}
        share.FETCH.start(ac, addr[0], addr[1] + 1, (body or {}).get('ids'))
        return share.FETCH.status()
    if path == '/api/fetchstatus':
        st = share.FETCH.status()
        if st.get('finished') and not st.get('rescanned'):
            _cache.clear(); share.FETCH.state['rescanned'] = True
        return st
    if path == '/api/fetchcancel':
        share.FETCH.cancel = True; return {'ok': True}
    if path == '/api/quit':                              # the user confirmed closing while hosting
        STATE['quit_ok'] = True
        threading.Thread(target=close_window, daemon=True).start()
        return {'ok': True}
    if path == '/api/log':
        return {'lines': log_tail(int(q.get('n', ['300'])[0]))}
    if path == '/api/probe':
        addr = read_code((body or {}).get('code', ''))
        if not addr: return {'error': "That code doesn't look right. It's 8 letters and numbers, like K7Q2-9XMA."}
        ip, port = addr
        try:
            info = http_get(f'http://{ip}:{port}/INFO', 5)
        except Exception:
            return {'error': "Can't reach the host. Check that Tailscale is on for both of you and the host shared their PC with you, and that their game is still hosted.",
                    'ip': ip, 'port': port}
        have = {c['id']: c for c in cars(ac)} if ac else {}
        tids = {t['id'] for t in tracks(ac)} if ac else set()
        track = (info.get('track') or '').split('/')[-1]
        cs = []
        for cid in dict.fromkeys(info.get('cars') or []):
            c = have.get(cid)
            cs.append({'id': cid, 'name': c['name'] if c else cid, 'installed': bool(c), 'traffic': 'traffic' in cid.lower()})
        rec = [r for r in s['recent_codes'] if r.get('code') != body['code']][:7]
        s['recent_codes'] = [{'code': body['code'], 'name': info.get('name'), 'when': time.time()}] + rec
        save_settings(s)
        out = {'ip': ip, 'port': port, 'info': info, 'cars': cs, 'track_installed': track in tids, 'track': track}
        try:
            man = share.compare(ac, share.manifest(ip, port + 1)) if ac else None
            if man:
                for it in man['items']: it.pop('files', None); it.pop('check', None)
                out['share'] = man
        except Exception as e:
            out['share_error'] = str(e)
        return out
    if path == '/api/join':
        addr = read_code((body or {}).get('code', '')) if (body or {}).get('code') else ('127.0.0.1', s['ports']['http'])
        if not addr: return {'error': 'Bad code.'}
        url = f'acmanager://race/online/join?ip={addr[0]}&httpPort={addr[1]}'
        try: os.startfile(url)
        except OSError: return {'error': 'Content Manager is not installed (the acmanager:// link did not open).'}
        if WINDOW['w']:                                  # the game is starting: get out of the way
            threading.Thread(target=lambda: (time.sleep(1.5), WINDOW['w'].minimize()), daemon=True).start()
        return {'ok': True, 'url': url}
    if path == '/api/show':
        if WINDOW['w']: WINDOW['w'].restore(); WINDOW['w'].show()
        return {'ok': True}
    if path == '/api/open':
        what = (body or {}).get('what')
        p = {'server': os.path.join(SRV, 'presets', PRESET), 'log': LOG, 'ac': ac}.get(what)
        if p and os.path.exists(p): os.startfile(p)
        return {'ok': True}
    return None


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, code, data, ctype='application/json'):
        b = data if isinstance(data, bytes) else json.dumps(data).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _host_ok(self):
        return (self.headers.get('Host') or '').split(':')[0] in ('127.0.0.1', 'localhost')

    def do_GET(self):
        if not self._host_ok(): return self._send(403, {'error': 'forbidden'})
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path.startswith('/api/'):
            r = api(u.path, q, None)
            return self._send(200 if r is not None else 404, r or {'error': 'not found'})
        if u.path == '/img':
            return self._img(q)
        f = os.path.normpath(os.path.join(UI_DIR, (u.path.lstrip('/') or 'index.html')))
        if not f.startswith(UI_DIR) or not os.path.isfile(f): return self._send(404, b'not found', 'text/plain')
        ct = {'.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml',
              '.png': 'image/png', '.ico': 'image/x-icon'}.get(os.path.splitext(f)[1], 'application/octet-stream')
        self._send(200, open(f, 'rb').read(), ct + ('; charset=utf-8' if ct.startswith('text') else ''))

    def _img(self, q):
        ac = find_ac(STATE['settings'])
        kind, i = q.get('k', [''])[0], q.get('id', [''])[0]
        lay = q.get('l', [''])[0]
        p = None
        if kind in ('preview', 'outline'):
            t = next((t for t in tracks(ac) if t['id'] == i and t['layout'] == lay), None)
            p = t and t[kind]
        elif kind == 'car':
            c = next((c for c in cars(ac) if c['id'] == i), None)
            p = c and c['preview']
        if not p or not os.path.isfile(p): return self._send(404, b'', 'text/plain')
        self._send(200, open(p, 'rb').read(), 'image/png' if p.endswith('.png') else 'image/jpeg')

    def do_POST(self):
        if not self._host_ok() or self.headers.get('Origin', f'http://127.0.0.1:{UI_PORT}') not in (
                f'http://127.0.0.1:{UI_PORT}', f'http://localhost:{UI_PORT}'):
            return self._send(403, {'error': 'forbidden'})
        n = int(self.headers.get('Content-Length') or 0)
        try: body = json.loads(self.rfile.read(n) or b'{}')
        except ValueError: body = {}
        u = urllib.parse.urlparse(self.path)
        r = api(u.path, urllib.parse.parse_qs(u.query), body)
        self._send(200 if r is not None else 404, r or {'error': 'not found'})


WINDOW = {'w': None}


def close_window():
    SERVER.stop(); share.HOST.stop()
    if WINDOW['w']: WINDOW['w'].destroy()


def on_closing():
    """X while hosting: the page asks first (closing ends the server); returning False keeps the window open"""
    if SERVER.running() and not STATE.get('quit_ok'):
        threading.Thread(target=lambda: WINDOW['w'].evaluate_js('window.confirmClose && confirmClose()'), daemon=True).start()
        return False
    return True


def open_edge():
    url = f'http://127.0.0.1:{UI_PORT}/'
    for edge in (os.path.join(os.environ.get('ProgramFiles(x86)', ''), r'Microsoft\Edge\Application\msedge.exe'),
                 os.path.join(os.environ.get('ProgramFiles', ''), r'Microsoft\Edge\Application\msedge.exe')):
        if os.path.isfile(edge):
            prof = os.path.join(os.environ.get('LOCALAPPDATA', ROOT), 'ACLink', 'window')
            subprocess.Popen([edge, f'--app={url}', '--window-size=1180,800', f'--user-data-dir={prof}',
                              '--no-first-run', '--disable-features=Translate'])
            return
    import webbrowser
    webbrowser.open(url)


def watchdog():
    """Edge fallback only: quit when the window is gone and no game is hosted"""
    while True:
        time.sleep(5)
        if time.time() - STATE['last_ping'] > 20 and not SERVER.running():
            os._exit(0)


def main():
    try:                                               # already running: bring its window forward
        req = urllib.request.Request(f'http://127.0.0.1:{UI_PORT}/api/show', data=b'{}', method='POST',
                                     headers={'Origin': f'http://127.0.0.1:{UI_PORT}', 'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=2).read()
        return
    except Exception:
        pass
    httpd = ThreadingHTTPServer(('127.0.0.1', UI_PORT), H)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    threading.Thread(target=lambda: (lambda ac: ac and (tracks(ac), cars(ac)))(find_ac(STATE['settings'])), daemon=True).start()
    threading.Thread(target=firewall, daemon=True).start()
    try:
        if '--no-window' in sys.argv:
            while True: time.sleep(3600)
        try:
            import webview
        except ImportError:
            webview = None
        if webview:
            w = webview.create_window(APP, f'http://127.0.0.1:{UI_PORT}/', width=1180, height=800, min_size=(900, 600),
                                      background_color='#262626', text_select=True)
            WINDOW['w'] = w
            w.events.closing += on_closing
            webview.start(private_mode=False,
                          storage_path=os.path.join(os.environ.get('LOCALAPPDATA', ROOT), 'ACLink', 'webview'))
        else:
            threading.Thread(target=watchdog, daemon=True).start()
            open_edge()
            while True: time.sleep(3600)
    finally:
        SERVER.stop(); share.HOST.stop()
        os._exit(0)


if __name__ == '__main__':
    main()
