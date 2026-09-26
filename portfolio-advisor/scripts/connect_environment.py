"""Connect the configured Luna session to OCI using Vault keys; submit no model input."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP),str(APP.parent),str(APP/'scripts')]
from portfolio_advisor.config import read_env,resolve_secrets,oci_auth,ConfigurationError
from portfolio_advisor.remote import validate_prepared_session,NeedsReview
from geniagent.app import State
from geniagent.oci_runtime import OCIRuntime
from geniagent.transport import CurlClient,APIError
from rollout import Rollout


def failure_message(exc):
    """Expose actionable failure categories without copying provider bodies."""
    if isinstance(exc, (ConfigurationError, NeedsReview)):
        return str(exc)
    if isinstance(exc, APIError) and 'DNS resolution failed during npm installation' in str(exc):
        return ('OCI setup failed: DNS resolution prevented npm installation. '
                'Inspect the retained setup command and project DNS policy before retrying. '
                'No model input was submitted.')
    status = getattr(exc, 'status', None)
    suffix = f' (HTTP {status})' if isinstance(status, int) and 100 <= status <= 599 else ''
    return ('Connection failed'+suffix+'. Inspect retained resource identifiers; no input was replayed. '
            'Provider bodies and credentials are suppressed.')


def replace_deleted_mapping(runtime, session_id, mapping):
    """Archive evidence only after the provider confirms the old sandbox is absent."""
    if not mapping.get('deleted') or not mapping.get('sandbox_id'):
        raise NeedsReview('Clean up the retained sandbox before requesting its replacement.')
    try:
        runtime.call('get_sandbox', mapping['sandbox_id'])
    except APIError as exc:
        if exc.status != 404:
            raise
    else:
        raise NeedsReview('Sandbox deletion is not yet confirmed; no replacement was created.')
    runtime.state.save(f'{session_id}-oci-previous-{uuid.uuid4().hex[:8]}.json', mapping)
    return None


def write_mapping(values,mapping):
    # Reload to preserve any independent operator edits. Only identifiers are saved.
    path=APP.parent/'.env';current=read_env(path)
    for key in ('OPENAI_BOOTSTRAP_SESSION_ID','OPENAI_PROJECT_ID','OCI_SANDBOX_PROJECT_ID'):
        if current.get(key)!=values.get(key):raise NeedsReview('Configuration changed during connection; saved IDs require reconciliation.')
    current['OPENAI_BOOTSTRAP_OCI_MAPPING']=json.dumps(mapping,separators=(',',':'))
    temporary=path.with_name('.env.connection-pending')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:
        for name,value in current.items():
            if '\n' in value or '\r' in value:raise NeedsReview('Multiline configuration requires operator review.')
            f.write(name+'='+value+'\n')
    os.replace(temporary,path);path.chmod(0o600)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--review-id',required=True)
    p.add_argument('--create-project',action='store_true',help='Create the dedicated managed-allowlist project if not configured.')
    p.add_argument('--replace-deleted',action='store_true',help='Archive a deleted sandbox mapping only after confirmed OCI 404, then replace its compute.')
    p.add_argument('--journal',type=Path,default=APP/'deploy/private/vault-bootstrap.json')
    args=p.parse_args()
    values=resolve_secrets(read_env(APP.parent/'.env'),names=('OPENAI_API_KEY','OPENAI_EXECUTOR_API_KEY'))
    sid,aid=values.get('OPENAI_BOOTSTRAP_SESSION_ID'),values.get('OPENAI_AGENT_ID')
    if not sid or not aid:raise ConfigurationError('Configure the dedicated OpenAI agent and session identifiers.')
    client=CurlClient(values['OPENAI_API_KEY'],values['OPENAI_PROJECT_ID'])
    session=client.request('GET','/agents/sessions/'+sid)
    validate_prepared_session(session,sid,aid,values.get('OPENAI_MODEL','gpt-5.6-luna'))
    for part in ('turns','items'):
        data=client.request('GET',f'/agents/sessions/{sid}/{part}')
        if data.get('data') or data.get('has_more'):raise NeedsReview('The prepared session has activity. No input was replayed or new compute submitted.')
    state=State(APP/'deploy/private/connection',values['OPENAI_PROJECT_ID'])
    with state.lock():
        import oci
        config,signer=oci_auth(values)
        if not values.get('OCI_SANDBOX_PROJECT_ID'):
            if not args.create_project:raise ConfigurationError('An approved sandbox project is required; no project was created.')
            rollout=Rollout(args.journal,values.get('OCI_PROFILE','Sandbox'))
            rollout.j.setdefault('reviews',[]).append({'id':args.review_id,'operation':'connect-environment','at':time.time()});rollout.save()
            project_id=rollout.project()
            deadline=time.monotonic()+180
            while True:
                project=rollout.client.get_generative_ai_project(project_id).data
                if project.lifecycle_state=='ACTIVE':break
                if project.lifecycle_state not in ('CREATING','UPDATING') or time.monotonic()>deadline:
                    raise NeedsReview('Project is not ACTIVE; its journaled ID is retained.')
                time.sleep(3)
            rollout.sync_references();values['OCI_SANDBOX_PROJECT_ID']=project_id
        sdk=oci.generative_ai_sandbox.GenerativeAiSandboxClient(config,signer=signer,
            service_endpoint=f"https://inference.generativeai.{values.get('OCI_REGION','us-chicago-1')}.oci.oraclecloud.com",
            retry_strategy=oci.retry.NoneRetryStrategy(),timeout=(5,20))
        runtime=OCIRuntime(state,lambda message:print(message,flush=True),sdk,oci.generative_ai_sandbox.models)
        for name in ('OPENAI_API_KEY','OPENAI_EXECUTOR_API_KEY','OCI_SANDBOX_PROJECT_ID'):
            os.environ[name]=values[name]
        runtime.preflight()
        eid=session['environment']['id']
        mapping=state.load(runtime.filename(sid))
        if mapping and mapping.get('oci_project_id')!=values['OCI_SANDBOX_PROJECT_ID']:
            raise NeedsReview('Saved OCI project differs from the approved configuration.')
        env=client.request('GET','/agents/environments/'+eid)
        if env.get('status')=='connected' and not mapping:
            raise NeedsReview('An environment is already connected without this OCI mapping; inspect its ownership before proceeding.')
        if mapping and args.replace_deleted:
            if env.get('status')=='connected':
                raise NeedsReview('The environment is connected; no replacement was created.')
            mapping=replace_deleted_mapping(runtime,sid,mapping)
        # Subscribe before starting the remote executor. No session input is submitted.
        with client.stream('GET',f'/agents/sessions/{sid}/events?stream=true',timeout=900):
            if mapping:
                runtime.health(sid)
            else:
                runtime.provision(session)
            deadline=time.monotonic()+120
            while True:
                runtime.health(sid)
                status=client.request('GET','/agents/environments/'+eid).get('status')
                if status=='connected':break
                if status in ('failed','expired') or time.monotonic()>deadline:
                    raise NeedsReview('OpenAI has not confirmed connection. Retained IDs prevent duplicate provisioning.')
                time.sleep(2)
        mapping=state.load(runtime.filename(sid))
        write_mapping(values,mapping)
        state.save('connection-evidence.json',{'session_id':sid,'environment_id':eid,'oci_project_id':mapping['oci_project_id'],
                    'sandbox_id':mapping['sandbox_id'],'executor_command_id':mapping['executor_command_id'],
                    'connected_at':time.time(),'model_input_submitted':False,'review_id':args.review_id})
        print('CONNECTED: OCI sandbox and executor are running; OpenAI confirms the environment connection. No model input was submitted.')
        print('The app will adopt this exact OCI mapping once for the first live analysis. Sandbox lifetime is 30 minutes.')


if __name__=='__main__':
    try:main()
    except Exception as exc:raise SystemExit(failure_message(exc)) from None
