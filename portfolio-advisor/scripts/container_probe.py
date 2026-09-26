"""Probe a LOCAL SYNTHETIC test container before/after replacement; never sends real holdings."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import uuid
import httpx

p=argparse.ArgumentParser(description=__doc__);p.add_argument('phase',choices=['before','after']);p.add_argument('--url',default='http://127.0.0.1:8781');p.add_argument('--record',type=Path,default=Path('/tmp/portfolio-container-probe.json'));args=p.parse_args()
if not args.url.startswith('http://127.0.0.1:'):raise SystemExit('This probe is restricted to local rehearsal.')
with httpx.Client(base_url=args.url,follow_redirects=True,timeout=10) as c:
    assert c.get('/api/config').json()['mode']=='rehearsal'
    c.get('/auth/login');c.headers['x-csrf-token']=c.get('/api/me').json()['csrf']
    if args.phase=='before':
        response=c.post('/api/v1/portfolios',data={'name':'Container replacement fixture'},files={'file':('holdings.csv',b'symbol,quantity,currency\nAAPL,10,USD\nCASH,100,USD\n')});response.raise_for_status();pid=response.json()['id']
        response=c.post(f'/api/v1/portfolios/{pid}/analyses',headers={'idempotency-key':str(uuid.uuid4())},json={'processingAcknowledged':True,'weights':{'VTI':60,'BND':35,'CASH':5}});response.raise_for_status();rid=response.json()['id']
        for _ in range(30):
            row=c.get('/api/v1/runs/'+rid).json()
            if row['status']=='completed':break
            time.sleep(.5)
        assert row['status']=='completed',row
        result=c.get(f'/api/v1/runs/{rid}/artifacts/json');result.raise_for_status()
        args.record.write_text(json.dumps({'portfolio':pid,'run':rid,'reportSha256':hashlib.sha256(result.content).hexdigest()}));args.record.chmod(0o600)
        print('Synthetic analysis and report saved in PostgreSQL and the test volume.')
    else:
        saved=json.loads(args.record.read_text());rid=saved['run']
        assert c.get('/api/v1/runs/'+rid).json()['status']=='completed'
        report=c.get(f'/api/v1/runs/{rid}/artifacts/json');report.raise_for_status()
        assert hashlib.sha256(report.content).hexdigest()==saved['reportSha256']
        print('PASS: history and identical report survived container replacement.')
    print(c.get('/healthz').json())
