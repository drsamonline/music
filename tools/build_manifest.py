#!/usr/bin/env python3
"""Build data/tracks.json (the manifest the page reads) from the crawled seed.

Rules encoded here:
  * cross-site duplicates merge into one row, strongest rights wins, others go to also_on
  * same-site same-title rows are separate takes and are never merged
  * albums come from title series (Flow/Suno), year buckets (SoundCloud) and fixed names
"""
import json, re, hashlib, datetime, sys
from urllib.parse import quote
from collections import defaultdict

SEED = sys.argv[1] if len(sys.argv) > 1 else '/scratch/output/music-sources-seed.json'
OUT = sys.argv[2] if len(sys.argv) > 2 else '/scratch/work/site/data/tracks.json'

seed = json.load(open(SEED))
tracks = [dict(t) for t in seed['tracks']]

FLOW_COVERS = {
    '400 BPM Techno': 'media/covers/400-bpm-techno.jpg',
    'Arena Awakens': 'media/covers/arena-awakens.jpg',
    'Beyond the Stars': 'media/covers/beyond-the-stars.jpg',
    'Black Oil Wire': 'media/covers/black-oil-wire.jpg',
    'Dawn Pursuit': 'media/covers/dawn-pursuit.jpg',
    'Phonk Sessions': 'media/covers/phonk-sessions.jpg',
    'Vocal Mayhem': 'media/covers/vocal-mayhem.jpg',
    'Singles': 'media/covers/flow-singles.jpg',
}

SITE_ORDER = {'soundcloud': 0, 'suno': 1, 'flow_music': 2, 'newgrounds': 3, 'freesound': 4}
SITE_LABEL = {'soundcloud': 'SoundCloud', 'suno': 'Suno', 'flow_music': 'Flow Music',
              'newgrounds': 'Newgrounds', 'freesound': 'Freesound'}


def norm(title):
    s = title.lower()
    s = re.sub(r'\s*\(take \d\)$', '', s)
    s = re.sub(r'\.(mp3|wav|m4a)$', '', s)
    s = re.sub(r'[^a-z0-9\u0900-\u097f ]', ' ', s)
    s = re.sub(r'\b(drsam|dr sam|meditation music)\b', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def slugify(s):
    s = re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')
    return s or 'x'


def fmt_dur(sec):
    sec = int(sec or 0)
    return f"{sec // 60}:{sec % 60:02d}"


# ---------- 1. merge cross-site duplicates ----------
groups = defaultdict(list)
for t in tracks:
    groups[norm(t['title'])].append(t)

kept, also = [], defaultdict(list)
for title, rows in groups.items():
    sites = {r['site'] for r in rows}
    if len(sites) > 1:
        # playable first (a player beats a link), then strongest rights, then site order
        rows.sort(key=lambda r: (0 if r.get('embed') == 'iframe' else 1,
                                 0 if r['rights_tier'] == 'commercial' else 1,
                                 SITE_ORDER.get(r['site'], 9)))
        primary = rows[0]
        for other in rows[1:]:
            also[id(primary)].append({
                'site': other['site'],
                'label': SITE_LABEL.get(other['site'], other['site']),
                'url': other.get('url'),
                'licence': other.get('licence'),
            })
        kept.append(primary)
    else:
        kept.extend(rows)

tracks = kept
for t in tracks:
    t['also_on'] = also.get(id(t), [])

# ---------- 2. album assignment ----------
SERIES = [
    ('flow_music', r'^Vocal Mayhem', 'Vocal Mayhem'),
    ('flow_music', r'^Dawn Pursuit', 'Dawn Pursuit'),
    ('flow_music', r'Beyond the Stars', 'Beyond the Stars'),
    ('flow_music', r'^Arena Awakens', 'Arena Awakens'),
    ('flow_music', r'^Black Oil Wire', 'Black Oil Wire'),
    ('flow_music', r'400 BPM Techno', '400 BPM Techno'),
    ('flow_music', r'Phonk', 'Phonk Sessions'),
    ('suno', r'Arena', 'Arena'),
]


def bucket(t):
    for site, pat, name in SERIES:
        if t['site'] == site and re.search(pat, t['title']):
            return name
    if t['site'] == 'soundcloud':
        return 'SoundCloud · ' + (t.get('created') or '0000')[:4]
    if t['site'] == 'newgrounds':
        return 'Newgrounds'
    if t['site'] == 'freesound':
        return 'Freesound'
    return 'Singles'


raw = defaultdict(list)
for t in tracks:
    raw[(t['site'], bucket(t))].append(t)

# any series with a single track falls back into Singles, before anything is rendered
final = defaultdict(list)
for (site, name), rows in raw.items():
    if len(rows) >= 2:
        final[(site, name)].extend(rows)
    else:
        final[(site, 'Singles')].extend(rows)

albums, out_tracks, used_slugs = [], [], set()
for (site, name), rows in sorted(final.items(),
                                 key=lambda kv: (SITE_ORDER.get(kv[0][0], 9), kv[0][1])):
    aid = 'alb_' + hashlib.sha1(f'{site}:{name}'.encode()).hexdigest()[:8]
    rows.sort(key=lambda t: (t.get('created') or '', t['title']))
    year = None
    years = [t.get('created', '')[:4] for t in rows if t.get('created')]
    if years:
        year = int(sorted(years)[-1])

    total_secs = sum(int(t.get('duration') or 0) for t in rows)
    cover_url = next((t.get('art') for t in rows if t.get('art')), None)
    if not cover_url and site == 'flow_music':
        cover_url = FLOW_COVERS.get(name)
    slug = slugify(name)
    if slug in used_slugs:
        slug = slug + '-' + slugify(SITE_LABEL[site])
    used_slugs.add(slug)
    albums.append({
        'id': aid,
        'slug': slug,
        'title': name,
        'source': site,
        'source_label': SITE_LABEL[site],
        'year': year,
        'cover_url': cover_url,
        'track_count': len(rows),
        'total_seconds': total_secs,
        'duration_label': fmt_dur(total_secs) if total_secs else '',
    })

    for pos, t in enumerate(rows, 1):
        embed = {
            'site': t['site'],
            'kind': 'iframe' if t.get('embed') == 'iframe' else 'link',
            'player_url': None,
            'source_url': t.get('url'),
        }
        if t['site'] == 'soundcloud':
            embed['player_url'] = ('https://w.soundcloud.com/player/?url='
                                   + quote(t.get('url') or '', safe=''))
        elif t['site'] == 'suno':
            embed['player_url'] = t.get('embed_url') or (
                'https://suno.com/embed/' + (t.get('song_id') or ''))

        link_pending = False
        if not embed['source_url']:
            prof = seed.get('profiles', {}).get(t['site'], {})
            embed['source_url'] = prof.get('audio') or prof.get('url')
            embed['link_pending'] = True
            link_pending = True

        out_tracks.append({
            'id': 'trk_' + hashlib.sha1(f"{t['site']}:{t.get('url') or t['title']}".encode()).hexdigest()[:8],
            'album_id': aid,
            'position': pos,
            'title': t['title'],
            'artist': 'Dr Sohil Momin',
            'duration': int(t.get('duration') or 0) if t.get('duration') else None,
            'duration_label': fmt_dur(t.get('duration')) if t.get('duration') else '—',
            'is_take': '(take ' in t['title'].lower(),
            'embed': embed,
            'rights': {
                'tier': t['rights_tier'],
                'label': 'Commercial use granted' if t['rights_tier'] == 'commercial' else 'Personal use only',
                'summary': t.get('rights_note') or (
                    'Streaming here is free. Commercial use of the file requires whatever the source platform asks for.'
                    if t['rights_tier'] == 'commercial' else
                    'Free to listen on this page. No commercial use.'),
                'licence': t.get('licence'),
            },
            'art': t.get('art'),
            'also_on': t.get('also_on', []),
            'link_pending': link_pending,
        })

# fill albums that ended up empty (shouldn't happen) and drop empty ones
counts = defaultdict(int)
for t in out_tracks:
    counts[t['album_id']] += 1
albums = [a for a in albums if counts[a['id']] > 0]

albums.sort(key=lambda a: (a['source'] != 'soundcloud', -(a['year'] or 0), a['title']))

manifest = {
    'schema_version': 2,
    'generated_at': datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z'),
    'hub_url': 'https://drsamonline.github.io/',
    'artist': 'Dr Sohil Momin',
    'counts': {
        'albums': len(albums),
        'tracks': len(out_tracks),
        'commercial': sum(1 for t in out_tracks if t['rights']['tier'] == 'commercial'),
        'personal': sum(1 for t in out_tracks if t['rights']['tier'] == 'personal'),
    },
    'albums': albums,
    'tracks': out_tracks,
}

import os
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump(manifest, open(OUT, 'w'), indent=1, ensure_ascii=False)

print(f"{len(albums)} albums, {len(out_tracks)} tracks, "
      f"{manifest['counts']['commercial']} commercial / {manifest['counts']['personal']} personal")
for a in albums:
    print(f"  {a['source_label']:12} {a['title']:34} {a['track_count']:3} tracks  {a['duration_label']}")
