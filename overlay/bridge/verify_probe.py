#!/usr/bin/env python3
"""Validate a completed JSONL capture from the WMP remote COM bridge.

The verifier fails closed when the ActiveX control created an isolated local
engine, when playback properties are missing, or when the capture lacks real
GUI geometry.  Validate a closed snapshot rather than a file still being
written so a partial final JSON line cannot be mistaken for bridge output.
"""
import json, pathlib, sys

# This is intentionally an offline verifier: it must not start WMP, mutate
# transport state, or infer success from the bridge process exit code alone.
p=pathlib.Path(sys.argv[1] if len(sys.argv)>1 else 'probe.jsonl')
assert p.exists(), f'missing live probe output: {p}'
rows=[json.loads(x) for x in p.read_text().splitlines() if x.strip()]
samples=[x for x in rows if x.get('type')=='sample']
assert samples, 'no actual samples'
assert all(x.get('isRemote') is True for x in samples), 'not connected to remote GUI engine'
assert any(x.get('sourceURL') for x in samples), 'missing currentMedia.sourceURL'
assert all(isinstance(x.get('playState'), int) and isinstance(x.get('currentPosition'), (int,float)) for x in samples), 'missing playback properties'
windows=[w for x in samples for w in x.get('windows',[])]
# Full-mode verification requires the WMPlayerApp root and at least one child
# rectangle.  Runtime HWND values and visualization class suffixes are unstable
# and therefore are never treated as constants here.
assert any(w['class']=='WMPlayerApp' and w['visible'] for w in windows), 'no visible actual WMP GUI'
assert any(w['parent'] and w['rect'][2]>0 and w['rect'][3]>0 for w in windows), 'no child geometry'
print(f'PASS: {len(samples)} remote samples, {len(windows)} window observations')
if '--require-pause' in sys.argv:
    # The bridge observes state only in normal mode.  A human or a separate GUI
    # harness must perform the play/pause action represented by these samples.
    paused=[x for x in samples if x['playState']==2]
    playing=[x for x in samples if x['playState']==3]
    assert len(paused)>=2 and playing, 'need parent-operated UI play/pause samples'
    assert any(abs(a['currentPosition']-b['currentPosition'])<0.1 for a,b in zip(paused,paused[1:])), 'pause did not hold position'
    print('PASS: playing and stable paused samples observed (operator must attest GUI action)')
