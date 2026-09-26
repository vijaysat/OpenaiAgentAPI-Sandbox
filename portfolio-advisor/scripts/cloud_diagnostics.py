"""Read only the dedicated service logs; never query the OCI audit log."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP), str(APP.parent), str(APP / 'scripts')]
from rollout import Rollout, private_json


def redact(message):
    message = re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[REDACTED_TOKEN]', str(message))
    message = re.sub(r'\bsk-[A-Za-z0-9_-]{12,}', '[REDACTED_KEY]', message)
    message = re.sub(r'(?i)(Bearer\s+)[^\s"\']+', r'\1[REDACTED]', message)
    return re.sub(r'(postgres(?:ql)?(?:\+psycopg)?://[^:/\s]+:)[^@\s]+@', r'\1[REDACTED]@', message)


def logs(r, kind, limit=25):
    management = r.oci.logging.LoggingManagementClient(r.config, signer=r.signer)
    groups = management.list_log_groups(r.j['outputs']['compartment_id'], display_name=r.j['name'] + '-operations').data
    if len(groups) != 1: raise ValueError('Dedicated log group could not be uniquely identified.')
    matches = management.list_logs(groups[0].id, display_name=r.j['name'] + '-' + kind).data
    if len(matches) != 1: raise ValueError('Dedicated service log is not available yet.')
    scope = '/'.join([r.j['outputs']['compartment_id'], groups[0].id, matches[0].id])
    query = 'search "' + scope + '"'
    if kind == 'build': query += ' | where data.buildRunId = \'' + r.j['image_build']['id'] + '\''
    query += ' | sort by datetime desc'
    result = r.oci.loggingsearch.LogSearchClient(r.config, signer=r.signer).search_logs(
        r.oci.loggingsearch.models.SearchLogsDetails(time_start=datetime.now(timezone.utc)-timedelta(hours=1),
            time_end=datetime.now(timezone.utc), search_query=query, is_return_field_info=False), limit=limit).data
    rows = []
    for item in result.results:
        content = item.data.get('logContent', {})
        if content.get('oracle', {}).get('loggroupid') == '_Audit':
            raise ValueError('Audit records rejected by service-log boundary.')
        data = content.get('data', {})
        rows.append({'time': content.get('time'), 'message': redact(data.get('message', ''))})
    private_json(r.path.parent / (kind + '-messages.json'), rows)
    return rows


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('kind', choices=['build', 'runtime']); p.add_argument('--limit', type=int, default=25)
    a = p.parse_args()
    try:
        r = Rollout(APP / 'deploy/private/vault-bootstrap.json', 'Sandbox')
        print(json.dumps(logs(r, a.kind, a.limit), indent=2))
    except Exception as exc:
        raise SystemExit(f'Dedicated service log read failed ({type(exc).__name__}, HTTP {getattr(exc, "status", None)}).') from None
