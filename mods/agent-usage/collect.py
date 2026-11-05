#!/usr/bin/env python3
"""Collects local usage facts for Claude Code, Codex and Antigravity and prints one JSON object.

Privacy: this reads counters, timestamps and booleans only. It never prints credentials,
account details or conversation text. Parsed transcripts are cached by file size and mtime in
~/.cache/herdr-agent-usage so repeated runs only read files that changed.
"""
import glob
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta

HOME = os.path.expanduser('~')
CACHE_DIR = os.path.join(HOME, '.cache', 'herdr-agent-usage')
NOW = time.time()


# ---------- helpers ----------

def read_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None


def load_cache(name):
    return read_json(os.path.join(CACHE_DIR, name)) or {'files': {}}


def save_cache(name, obj):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = os.path.join(CACHE_DIR, '%s.tmp%d' % (name, os.getpid()))
        with open(tmp, 'w') as fh:
            json.dump(obj, fh)
        os.replace(tmp, os.path.join(CACHE_DIR, name))
    except Exception:
        pass


def local_day(ts):
    try:
        return datetime.fromisoformat(ts.replace('Z', '+00:00')).astimezone().date().isoformat()
    except Exception:
        return None


def epoch(ts):
    try:
        return datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()
    except Exception:
        return None


def window_starts():
    today = datetime.now().astimezone().date()
    return {'today': today.isoformat(),
            'week': (today - timedelta(days=6)).isoformat(),
            'month': (today - timedelta(days=29)).isoformat()}


def newest_mtime(paths):
    best = 0
    for p in paths:
        try:
            best = max(best, os.path.getmtime(p))
        except Exception:
            pass
    return best or None


# ---------- Claude Code ----------

def claude():
    base = os.path.join(HOME, '.claude')
    out = {'installed': os.path.isdir(base)}
    if not out['installed']:
        return out

    acct = (read_json(os.path.join(HOME, '.claude.json')) or {}).get('oauthAccount') or {}
    out['signed_in'] = bool(acct)
    org = acct.get('organizationType') or ''
    out['plan'] = {'claude_max': 'Max', 'claude_pro': 'Pro', 'claude_team': 'Team',
                   'claude_enterprise': 'Enterprise'}.get(org) or (acct.get('billingType') and 'Account') or None

    # Rate limits are only known to a running session; a status line that saves its input file can leave the last ones it saw.
    hud = os.path.join(base, 'hud', 'cache', 'last-input.json')
    rl = (read_json(hud) or {}).get('rate_limits')
    if rl:
        limits = []
        for key, label, minutes in (('five_hour', '5-hour', 300), ('seven_day', 'Weekly', 10080)):
            w = rl.get(key)
            if w:
                limits.append({'label': label, 'used': w.get('used_percentage'),
                               'resets_at': w.get('resets_at'), 'window_minutes': minutes})
        out['limits'] = limits
        out['limits_as_of'] = newest_mtime([hud])

    cache = load_cache('claude.json')
    files = cache.get('files', {})
    live = {}
    for path in glob.glob(os.path.join(base, 'projects', '**', '*.jsonl'), recursive=True):
        try:
            st = os.stat(path)
        except Exception:
            continue
        ent = files.get(path)
        if ent and ent.get('m') == st.st_mtime and ent.get('s') == st.st_size:
            live[path] = ent
            continue
        msgs = {}
        try:
            with open(path, errors='replace') as fh:
                for line in fh:
                    if '"assistant"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if d.get('type') != 'assistant':
                        continue
                    m = d.get('message') or {}
                    u = m.get('usage')
                    mid = m.get('id')
                    model = m.get('model') or ''
                    if not u or not mid or model.startswith('<'):
                        continue
                    msgs[mid] = [d.get('timestamp'), model, u.get('input_tokens') or 0,
                                 u.get('output_tokens') or 0, u.get('cache_read_input_tokens') or 0,
                                 u.get('cache_creation_input_tokens') or 0]
        except Exception:
            pass
        live[path] = {'m': st.st_mtime, 's': st.st_size, 'msgs': msgs}
    save_cache('claude.json', {'files': live})

    merged = {}
    for ent in live.values():
        for mid, rec in ent['msgs'].items():
            merged[mid] = rec  # a message resumed into another transcript counts once

    starts = window_starts()
    totals = {k: {'messages': 0, 'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0} for k in starts}
    models = {}
    last = 0
    for ts, model, i, o, cr, cw in merged.values():
        day = local_day(ts or '')
        if not day:
            continue
        e = epoch(ts)
        if e:
            last = max(last, e)
        for k, start in starts.items():
            if day >= start:
                t = totals[k]
                t['messages'] += 1
                t['input'] += i
                t['output'] += o
                t['cache_read'] += cr
                t['cache_write'] += cw
        if day >= starts['week']:
            models[model] = models.get(model, 0) + i + o
    out['totals'] = totals
    out['top_models'] = sorted(({'model': m, 'tokens': n} for m, n in models.items()),
                               key=lambda x: -x['tokens'])[:3]
    out['last_active'] = last or None
    return out


# ---------- Codex ----------

def codex():
    base = os.path.join(HOME, '.codex')
    out = {'installed': os.path.isdir(base)}
    if not out['installed']:
        return out

    auth = read_json(os.path.join(base, 'auth.json')) or {}
    out['signed_in'] = bool(auth.get('tokens')) or bool(auth.get('OPENAI_API_KEY'))
    out['auth_mode'] = auth.get('auth_mode')

    cache = load_cache('codex.json')
    files = cache.get('files', {})
    live = {}
    for path in glob.glob(os.path.join(base, 'sessions', '**', 'rollout-*.jsonl'), recursive=True):
        try:
            st = os.stat(path)
        except Exception:
            continue
        ent = files.get(path)
        if ent and ent.get('m') == st.st_mtime and ent.get('s') == st.st_size:
            live[path] = ent
            continue
        days = {}
        prev = None
        rl_last = None
        last_ts = None
        try:
            with open(path, errors='replace') as fh:
                for line in fh:
                    if '"token_count"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    p = d.get('payload') or {}
                    if p.get('type') != 'token_count':
                        continue
                    ts = d.get('timestamp') or ''
                    info = p.get('info')
                    if info and info.get('total_token_usage'):
                        tot = info['total_token_usage']
                        cur = [tot.get('input_tokens') or 0, tot.get('cached_input_tokens') or 0,
                               tot.get('output_tokens') or 0, tot.get('reasoning_output_tokens') or 0,
                               tot.get('total_tokens') or 0]
                        if prev is None or cur[4] < prev[4]:
                            delta = cur
                        else:
                            delta = [c - q for c, q in zip(cur, prev)]
                        prev = cur
                        day = local_day(ts)
                        if day and any(delta):
                            acc = days.setdefault(day, [0, 0, 0, 0, 0, 0])
                            for idx in range(5):
                                acc[idx] += delta[idx]
                            acc[5] += 1
                        last_ts = ts
                    if p.get('rate_limits'):
                        rl_last = {'ts': ts, 'rl': p['rate_limits']}
        except Exception:
            pass
        live[path] = {'m': st.st_mtime, 's': st.st_size, 'days': days, 'rl': rl_last, 'last_ts': last_ts}
    save_cache('codex.json', {'files': live})

    starts = window_starts()
    totals = {k: {'requests': 0, 'input': 0, 'cached': 0, 'output': 0, 'total': 0} for k in starts}
    latest_rl = None
    last = 0
    for ent in live.values():
        for day, v in ent['days'].items():
            for k, start in starts.items():
                if day >= start:
                    t = totals[k]
                    t['input'] += v[0] - v[1]
                    t['cached'] += v[1]
                    t['output'] += v[2]
                    t['total'] += v[4]
                    t['requests'] += v[5]
        if ent.get('rl') and (latest_rl is None or ent['rl']['ts'] > latest_rl['ts']):
            latest_rl = ent['rl']
        e = epoch(ent.get('last_ts') or '')
        if e:
            last = max(last, e)
    out['totals'] = totals
    out['last_active'] = last or None

    if latest_rl:
        rl = latest_rl['rl']
        out['plan'] = rl.get('plan_type')
        limits = []
        for key in ('primary', 'secondary'):
            w = rl.get(key)
            if w:
                minutes = w.get('window_minutes')
                label = {300: '5-hour', 10080: 'Weekly'}.get(minutes) or ('%s min' % minutes)
                limits.append({'label': label, 'used': w.get('used_percent'),
                               'resets_at': w.get('resets_at'), 'window_minutes': minutes})
        out['limits'] = limits
        out['limits_as_of'] = epoch(latest_rl['ts'])
    return out


# ---------- Antigravity ----------

def antigravity():
    gem = os.path.join(HOME, '.gemini', 'antigravity')
    apps = ['/Applications/Antigravity.app', '/Applications/Antigravity IDE.app']
    out = {'installed': os.path.isdir(gem) or any(os.path.isdir(a) for a in apps)}
    if not out['installed']:
        return out

    signed_in = False
    for app in ('Antigravity', 'Antigravity IDE'):
        db = os.path.join(HOME, 'Library', 'Application Support', app, 'User', 'globalStorage', 'state.vscdb')
        if not os.path.exists(db):
            continue
        try:
            con = sqlite3.connect('file:%s?mode=ro' % db, uri=True, timeout=1)
            row = con.execute("select length(value) from ItemTable where key = ?",
                              ('antigravityUnifiedStateSync.oauthToken',)).fetchone()
            con.close()
            if row and row[0] and row[0] > 0:
                signed_in = True
        except Exception:
            pass
    out['signed_in'] = signed_in

    convos = None
    db = os.path.join(gem, 'conversation_summaries.db')
    if os.path.exists(db):
        try:
            con = sqlite3.connect('file:%s?mode=ro' % db, uri=True, timeout=1)
            convos = con.execute('select count(*) from conversation_summaries').fetchone()[0]
            con.close()
        except Exception:
            pass
    out['conversations'] = convos
    out['last_active'] = newest_mtime(glob.glob(os.path.join(gem, 'log', '*')) + [db])
    return out


def safe(fn):
    try:
        return fn()
    except Exception as e:
        return {'installed': True, 'error': str(e)[:200]}


if __name__ == '__main__':
    json.dump({'generated_at': NOW, 'claude': safe(claude), 'codex': safe(codex),
               'antigravity': safe(antigravity)}, sys.stdout)
