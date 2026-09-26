import sys
from pathlib import Path

import pytest
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from connect_environment import failure_message, replace_deleted_mapping, NeedsReview
from geniagent.transport import APIError


@pytest.mark.parametrize('error, expected', [
    (APIError('sensitive-body-value', status=403), 'HTTP 403'),
    (RuntimeError('sensitive-body-value'), 'Connection failed'),
    (APIError('DNS resolution failed during npm installation; sensitive-body-value'),
     'DNS resolution prevented npm installation'),
])
def test_connection_failure_retains_category_without_provider_body(error, expected):
    result = failure_message(error)
    assert expected in result
    assert 'sensitive-body-value' not in result


@pytest.mark.parametrize('status', [404, 401, 200])
def test_replacement_requires_confirmed_absence_and_preserves_evidence(status):
    writes=[]
    mapping={'sandbox_id':'old-sandbox','deleted':True,'setup_command_id':'failed-setup'}
    def get(*args):
        if status!=200:raise APIError('provider body',status=status)
        return NS()
    runtime=NS(call=get,state=NS(save=lambda name,value:writes.append((name,value))))
    if status==404:
        assert replace_deleted_mapping(runtime,'session',mapping) is None
        assert len(writes)==1 and writes[0][1]==mapping
    else:
        with pytest.raises((APIError,NeedsReview)):
            replace_deleted_mapping(runtime,'session',mapping)
        assert not writes
