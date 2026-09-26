"""Journaled rollout CLI. Cloud mutations require the separately approved review ID.
No secret values are accepted as arguments, written to disk, or included in errors.
"""
import argparse
import base64
import getpass
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
from urllib.parse import urlsplit
import uuid

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP), str(APP.parent)]
from portfolio_advisor.config import SECRET_NAMES, oci_auth, read_env
from check_oci_capabilities import check


def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.pending')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f: json.dump(value, f, indent=2); f.write('\n')
    os.replace(temporary, path)
    path.chmod(0o600)


class Rollout:
    def __init__(self, path, profile):
        import oci
        initial = path.read_bytes()
        self.path, self.j = path, json.loads(initial)
        self._journal_version = hashlib.sha256(initial).hexdigest()
        self.config, self.signer = oci_auth({'OCI_PROFILE': profile, 'OCI_REGION': self.j['outputs']['region']})
        self.client = oci.generative_ai.GenerativeAiClient(self.config, signer=self.signer, retry_strategy=oci.retry.NoneRetryStrategy())
        self.vault = oci.vault.VaultsClient(self.config, signer=self.signer, retry_strategy=oci.retry.NoneRetryStrategy())
        self.oci = oci
        self.tags = {'application': 'portfolio-advisor', 'deployment_id': self.j['id']}

    def sandbox_project_id(self):
        """Select new work without changing the original creation/move receipts."""
        selected = self.j.get('activeSandboxProject')
        if selected:
            if not selected.get('verifiedCommandId') or not selected.get('id'):
                raise ValueError('Selected sandbox project has no verified execution evidence.')
            return selected['id']
        return self.j['operations'].get('project', {}).get('id')

    def select_sandbox_project(self, project_id, sandbox_id, command_id):
        """Adopt an independently tested, owned project for future analyses only."""
        previous = self.sandbox_project_id()
        if not all((previous, project_id, sandbox_id, command_id)):
            raise ValueError('Supply the project and its successful sandbox/command identifiers.')
        current = self.client.get_generative_ai_project(previous).data
        target = self.client.get_generative_ai_project(project_id).data
        for project in (current, target):
            if (project.compartment_id != self.j['outputs']['compartment_id'] or
                    (project.freeform_tags or {}).get('deployment_id') != self.j['id']):
                raise ValueError('Sandbox project ownership differs from this rollout.')
        if (target.lifecycle_state != 'ACTIVE' or not target.sandbox_config.is_enabled or
                self.oci.util.to_dict(target.sandbox_config) != self.oci.util.to_dict(current.sandbox_config)):
            raise ValueError('Replacement must be ACTIVE with the same approved sandbox settings.')
        sdk = self.oci.generative_ai_sandbox.GenerativeAiSandboxClient(self.config, signer=self.signer,
            service_endpoint=f"https://inference.generativeai.{self.j['outputs']['region']}.oci.oraclecloud.com",
            timeout=(5, 20), retry_strategy=self.oci.retry.NoneRetryStrategy())
        sandbox = sdk.get_sandbox(sandbox_id).data
        command = sdk.get_sandbox_command(sandbox_id, command_id).data
        output = sdk.get_sandbox_command_output(sandbox_id, command_id).data
        if (sandbox.project_id != project_id or command.exit_code != 0 or
                not (output.stdout or '').startswith('OCI_STARTUP_READY ')):
            raise ValueError('Successful readiness execution in the replacement project is unconfirmed.')
        if project_id == previous:
            return {'projectId': project_id, 'status': 'already_selected'}
        selection = {'id': project_id, 'previousId': previous, 'verifiedSandboxId': sandbox_id,
                     'verifiedCommandId': command_id, 'selectedAt': time.time()}
        self.j.setdefault('sandboxProjectHistory', []).append(selection.copy())
        self.j['activeSandboxProject'] = selection
        self.save()
        return {'projectId': project_id, 'previousId': previous, 'status': 'selected_for_new_analyses'}

    def save(self):
        # Concurrent setup commands must not overwrite another command's evidence.
        lock_path = self.path.with_suffix('.lock')
        with os.fdopen(os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600), 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            expected = getattr(self, '_journal_version', None)
            if expected and hashlib.sha256(self.path.read_bytes()).hexdigest() != expected:
                raise ValueError('Rollout journal changed in another process; reload and reconcile before continuing.')
            private_json(self.path, self.j)
            self._journal_version = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def operation(self, name, call, identify=lambda response: response.data.id):
        previous = self.j['operations'].get(name)
        if previous:
            if previous.get('id'): return previous['id']
            raise ValueError('An earlier request is unconfirmed. Reconcile its recorded retry token before continuing.')
        retry = str(uuid.uuid5(uuid.UUID(self.j['id']), name))
        self.j['operations'][name] = {'started': time.time(), 'retryToken': retry}
        self.save()  # Durable intent before any provider request.
        response = call(retry)
        identifier = identify(response)
        self.j['operations'][name].update(id=identifier, workRequest=response.headers.get('opc-work-request-id'))
        self.save()
        return identifier

    def vault_foundation(self):
        """Bootstrap only the separately authorized Vault; import it into Terraform later."""
        m, o = self.oci.key_management.models, self.j['outputs']
        client = self.oci.key_management.KmsVaultClient(self.config, signer=self.signer)
        if 'vault' not in self.j['operations']:
            existing = self.oci.pagination.list_call_get_all_results(client.list_vaults, o['compartment_id']).data
            if any(v.display_name == self.j['name']+'-vault' and v.lifecycle_state != 'DELETED' for v in existing):
                raise ValueError('A Vault with the dedicated name exists. Reconcile ownership before creating another.')
        vid = self.operation('vault', lambda token: client.create_vault(m.CreateVaultDetails(
            compartment_id=o['compartment_id'], display_name=self.j['name']+'-vault', vault_type='DEFAULT',
            freeform_tags=self.tags), opc_retry_token=token))
        vault = client.get_vault(vid).data
        o['vault_id'] = vid; self.save()
        if vault.lifecycle_state != 'ACTIVE':
            return {'vaultId':vid, 'state':vault.lifecycle_state, 'next':'Repeat vault-foundation to continue the same journaled resources.'}
        management = self.oci.key_management.KmsManagementClient(self.config, signer=self.signer, service_endpoint=vault.management_endpoint)
        kid = self.operation('vault-key', lambda token: management.create_key(m.CreateKeyDetails(
            compartment_id=o['compartment_id'], display_name=self.j['name']+'-key', protection_mode='SOFTWARE',
            key_shape=m.KeyShape(algorithm='AES', length=32), freeform_tags=self.tags), opc_retry_token=token))
        key = management.get_key(kid).data
        o['key_id'] = kid; self.save()
        return {'vaultId':vid,'keyId':kid,'state':key.lifecycle_state,'managementEndpoint':vault.management_endpoint}

    def sync_references(self, remove_keys=()):
        """Write only actual identifiers, never credential values, into the local environment."""
        path = APP.parent/'.env'; values = read_env(path); o = self.j['outputs']
        for name in remove_keys: values.pop(name,None)
        values.update(OCI_COMPARTMENT_ID=o['compartment_id'], OCI_REGION=o['region'])
        for source, target in [('vault_id','OCI_VAULT_ID'),('key_id','OCI_VAULT_KEY_ID')]:
            if o.get(source): values[target] = o[source]
        project = self.sandbox_project_id()
        if project: values['OCI_SANDBOX_PROJECT_ID'] = project
        values.update({name:json.dumps({'secretId':sid},separators=(',',':')) for name,sid in self.j['secrets'].items() if name in SECRET_NAMES})
        for name in self.j.get('pending_replacement_secrets',{}):
            values[name]=json.dumps({'secretId':''},separators=(',',':'))
        temporary = path.with_name('.env.pending')
        fd = os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as f:
            for name,value in values.items():
                if '\n' in value or '\r' in value: raise ValueError('Unexpected multiline environment value; original file preserved.')
                f.write(name+'='+value+'\n')
        os.replace(temporary,path);path.chmod(0o600)
        return 'Actual Vault references saved; unresolved credentials remain explicitly pending.'

    def seed_openai(self):
        """The operator supplies genuine OpenAI credentials through hidden terminal input."""
        for variable in ('OPENAI_API_KEY','OPENAI_EXECUTOR_API_KEY'):
            self.seed(variable)
        return self.sync_references()

    def import_key(self, alias, variable):
        """Import the user's explicit temporary alias, then remove plaintext after verification."""
        value = read_env(APP.parent/'.env').get(alias,'')
        if not value.strip() or value.lstrip().startswith('{'):
            raise ValueError(alias+' must contain the supplied credential for this import.')
        client = self.oci.secrets.SecretsClient(self.config,signer=self.signer)
        other = 'OPENAI_EXECUTOR_API_KEY' if variable=='OPENAI_API_KEY' else 'OPENAI_API_KEY'
        other_id = self.j['secrets'].get(other)
        if other_id:
            other_value = base64.b64decode(client.get_secret_bundle(other_id,stage='CURRENT').data.secret_bundle_content.content).decode()
            if secrets.compare_digest(value,other_value): raise ValueError('The application and executor keys must differ.')
        pending = self.j.get('pending_replacement_secrets',{}).get(variable) or self.j['secrets'].get(variable)
        same = False
        if pending:
            current = client.get_secret_bundle(pending,stage='CURRENT').data.secret_bundle_content
            same = secrets.compare_digest(value,base64.b64decode(current.content,validate=True).decode())
        if pending and not same:
            metadata = self.vault.get_secret(pending)
            if metadata.data.vault_id != self.j['outputs']['vault_id']: raise ValueError('Replacement secret is outside the dedicated Vault.')
            m=self.oci.vault.models
            imports=self.j.setdefault('credential_imports',{})
            operation=imports.setdefault(variable,'replace-credential:'+variable+':'+str(uuid.uuid4()))
            self.save()
            sid=self.operation(operation,lambda token:self.vault.update_secret(pending,
                m.UpdateSecretDetails(secret_content=m.Base64SecretContentDetails(content_type='BASE64',stage='CURRENT',content=base64.b64encode(value.encode()).decode())),
                if_match=metadata.headers['etag']),lambda _:pending)
        elif pending:
            sid=pending
        else:
            sid = self.seed(variable,value)
        bundle = client.get_secret_bundle(sid,stage='CURRENT').data.secret_bundle_content
        stored = base64.b64decode(bundle.content,validate=True).decode()
        if not secrets.compare_digest(value,stored):
            raise ValueError('The CURRENT Vault secret differs for '+variable+'; wait for the journaled update or reconcile its version before removing the local alias.')
        self.j['secrets'][variable]=sid
        self.j.get('pending_replacement_secrets',{}).pop(variable,None)
        self.j.get('credential_imports',{}).pop(variable,None);self.save()
        return self.sync_references(remove_keys=(alias,))

    def import_app_key(self):
        values=read_env(APP.parent/'.env')
        alias='OPENAI_APPLICATION_KEY' if 'OPENAI_APPLICATION_KEY' in values else 'OPENAI_AGENT_APIKEY'
        self.verify_application_key(values.get(alias,''),values)
        return self.import_key(alias,'OPENAI_API_KEY')

    def verify_application_key(self,value,values):
        from geniagent.transport import CurlClient,APIError
        if not value.strip():raise ValueError('Supply OPENAI_APPLICATION_KEY through the protected local environment.')
        try:
            agent=CurlClient(value,values['OPENAI_PROJECT_ID']).request('GET','/agents/'+values['OPENAI_AGENT_ID'])
        except APIError as exc:
            hint = (' Application requests require api.agents.read, api.agents.write and api.responses.write; '
                    'a restricted environment key is only for the sandbox executor.') if exc.status == 403 else ''
            raise ValueError(f'OpenAI rejected the supplied application key (HTTP {exc.status}); Vault was not changed.'+hint) from None
        if agent.get('id')!=values['OPENAI_AGENT_ID'] or agent.get('model')!=values.get('OPENAI_MODEL','gpt-5.6-luna'):
            raise ValueError('The application key did not verify the configured agent and model.')

    def import_executor_key(self):
        return self.import_key('OPENAI_ENVIRONMENT_KEY','OPENAI_EXECUTOR_API_KEY')

    def reclassify_executor_key(self):
        """Apply the user's correction: the supplied key is an environment credential."""
        pending=self.j.setdefault('pending_replacement_secrets',{})
        sid=pending.get('OPENAI_API_KEY') or self.j['secrets'].get('OPENAI_API_KEY')
        if not sid:raise ValueError('No supplied key is available to reclassify.')
        client=self.oci.secrets.SecretsClient(self.config,signer=self.signer)
        value=base64.b64decode(client.get_secret_bundle(sid,stage='CURRENT').data.secret_bundle_content.content).decode()
        pending['OPENAI_API_KEY']=sid;self.j['secrets'].pop('OPENAI_API_KEY',None);self.save()
        target=self.seed('OPENAI_EXECUTOR_API_KEY',value)
        stored=base64.b64decode(client.get_secret_bundle(target,stage='CURRENT').data.secret_bundle_content.content).decode()
        if not secrets.compare_digest(value,stored):raise ValueError('Executor secret verification failed; inspect the dedicated versions.')
        return self.sync_references()

    def project(self):
        if self.j.get('activeSandboxProject'):
            return self.sandbox_project_id()
        m = self.oci.generative_ai.models
        details = m.CreateGenerativeAiProjectDetails(compartment_id=self.j['outputs']['compartment_id'],
            display_name=self.j['name']+'-sandbox', freeform_tags=self.tags,
            sandbox_config=m.SandboxConfig(is_enabled=True, is_persistence_enabled=False,
                sandbox_outbound_networking_config=m.ManagedSandboxOutboundNetworkingConfig(network_mode='MANAGED',
                    network_policy=m.SandboxAllowNetworkPolicy(mode='ALLOW', allowed_domains=[m.AllowedDomain(
                        hostnames=['api.openai.com', 'codex-cloud-environments.chatgpt.com',
                                   'registry.npmjs.org', 'query1.finance.yahoo.com'], ports=[443])]))))
        return self.operation('project', lambda token: self.client.create_generative_ai_project(details, opc_retry_token=token))

    def application(self):
        m, o = self.oci.generative_ai.models, self.j['outputs']
        details = m.CreateHostedApplicationDetails(compartment_id=o['compartment_id'], display_name=self.j['name'],
            freeform_tags=self.tags, inbound_auth_config=m.InboundAuthConfig(inbound_auth_config_type='NO_AUTH_CONFIG'),
            scaling_config=m.ScalingConfig(min_replica=1, max_replica=1, scaling_type='CPU', target_cpu_threshold=70),
            networking_config=m.NetworkingConfig(inbound_networking_config=m.InboundNetworkingConfig(endpoint_mode='PUBLIC'),
                outbound_networking_config=m.OutboundNetworkingConfig(network_mode='CUSTOM',
                    custom_subnet_id=o['application_subnet_id'], nsg_ids=o['application_nsg_ids'])))
        aid = self.operation('application', lambda token: self.client.create_hosted_application(details, opc_retry_token=token))
        actual = self.client.get_hosted_application(aid).data
        if actual.freeform_tags.get('deployment_id') != self.j['id']: raise ValueError('Application ownership mismatch.')
        network = actual.networking_config.outbound_networking_config
        if network.custom_subnet_id != o['application_subnet_id'] or set(network.nsg_ids) != set(o['application_nsg_ids']):
            raise ValueError('Application network does not match the approved rollout.')
        self.j['applicationURL'] = f"https://inference.generativeai.{o['region']}.oci.oraclecloud.com/20251112/hostedApplications/{aid}/actions/invoke"
        self.save()
        return aid

    def adopt_foundation(self):
        """Move only this rollout's sandbox project into its dedicated compartment."""
        o = self.j['foundation_outputs']
        if o['vault_id'] != self.j['outputs']['vault_id'] or o['key_id'] != self.j['outputs']['key_id']:
            raise ValueError('Foundation credential resources differ from the bootstrap.')
        pid = self.j['operations']['project']['id']
        response = self.client.get_generative_ai_project(pid)
        if response.data.freeform_tags.get('deployment_id') != self.j['id']:
            raise ValueError('Sandbox project ownership differs from this rollout.')
        if response.data.compartment_id != o['compartment_id']:
            if response.data.compartment_id != o['vault_compartment_id']:
                raise ValueError('Sandbox project is outside the approved source compartment.')
            self.operation('move-project:' + o['compartment_id'], lambda token:
                self.client.change_generative_ai_project_compartment(pid,
                    self.oci.generative_ai.models.ChangeGenerativeAiProjectCompartmentDetails(
                        compartment_id=o['compartment_id']), if_match=response.headers['etag'],
                    opc_retry_token=token), lambda _: pid)
        current = self.client.get_generative_ai_project(pid).data
        if current.compartment_id != o['compartment_id']:
            raise ValueError('Sandbox project move is pending; inspect its work request before continuing.')
        self.j['outputs'] = o; self.save()
        return {'compartment_id': o['compartment_id'], 'sandbox_project_preserved': True}

    def prepare_deployment(self, container_uri, tag):
        m = self.oci.generative_ai.models
        existing = self.j['operations'].get('deployment', {})
        if existing.get('id'): return existing['id']
        if existing and existing.get('rejected_http') != 400:
            raise ValueError('Earlier deployment creation remains uncertain; reconcile it first.')
        key = 'deployment-create:' + tag
        did = self.operation(key, lambda token: self.client.create_hosted_deployment(m.CreateHostedDeploymentDetails(
            display_name=self.j['name'], compartment_id=self.j['outputs']['compartment_id'],
            hosted_application_id=self.j['operations']['application']['id'], freeform_tags=self.tags,
            active_artifact=m.SingleDockerArtifact(artifact_type='SIMPLE_DOCKER_ARTIFACT',
                container_uri=container_uri, tag=tag, status='ACTIVE')), opc_retry_token=token))
        if existing: self.j.setdefault('rejected_operations', []).append({'operation': 'deployment', **existing})
        self.j['operations']['deployment'] = dict(self.j['operations'][key]); self.save()
        current = self.client.get_hosted_deployment(did).data
        if current.hosted_application_id != self.j['operations']['application']['id']:
            raise ValueError('Deployment belongs to another application.')
        return did

    def seed(self, variable, value=None):
        name = SECRET_NAMES.get(variable, 'portfolio-advisor-postgres-admin-password' if variable == 'POSTGRES_ADMIN_PASSWORD' else None)
        if not name: raise ValueError('Unknown dedicated credential name.')
        if variable in self.j['secrets']: return self.j['secrets'][variable]
        compartment = self.j['outputs'].get('vault_compartment_id', self.j['outputs']['compartment_id'])
        matches = self.oci.pagination.list_call_get_all_results(self.vault.list_secrets,
            compartment_id=compartment, name=name).data
        if matches: raise ValueError('Secret name already exists. Verify ownership and reconcile the journal; it will not be reused automatically.')
        if value is None:
            value = secrets.token_urlsafe(48) if variable in ('AUTH_SESSION_SECRET', 'POSTGRES_ADMIN_PASSWORD') else getpass.getpass(f'{variable} (stored directly in Vault): ')
        if not value.strip(): raise ValueError('An empty credential was rejected.')
        if variable in ('OPENAI_API_KEY', 'OPENAI_EXECUTOR_API_KEY'):
            other = 'OPENAI_EXECUTOR_API_KEY' if variable == 'OPENAI_API_KEY' else 'OPENAI_API_KEY'
            if other in self.j['secrets']:
                client = self.oci.secrets.SecretsClient(self.config, signer=self.signer)
                previous = base64.b64decode(client.get_secret_bundle(self.j['secrets'][other], stage='CURRENT').data.secret_bundle_content.content).decode()
                if secrets.compare_digest(previous, value): raise ValueError('The application and executor keys must differ.')
        m = self.oci.vault.models
        details = m.CreateSecretDetails(compartment_id=compartment, secret_name=name,
            vault_id=self.j['outputs']['vault_id'], key_id=self.j['outputs']['key_id'], freeform_tags=self.tags,
            secret_content=m.Base64SecretContentDetails(content_type='BASE64', stage='CURRENT', content=base64.b64encode(value.encode()).decode()))
        sid = self.operation('secret:'+variable, lambda token: self.vault.create_secret(details, opc_retry_token=token))
        self.j['secrets'][variable] = sid
        self.save()
        return sid

    def sso(self, domain):
        if 'sso' in self.j['operations']: raise ValueError('SSO already created or uncertain. Reconcile it before repeating.')
        if not domain or not domain.startswith('https://'): raise ValueError('Use the approved Identity Domain HTTPS URL.')
        import urllib.request
        discovery_url = domain.rstrip('/') + '/.well-known/openid-configuration'
        with urllib.request.urlopen(discovery_url, timeout=15) as response:
            discovery = json.load(response)
        if not discovery.get('issuer', '').startswith('https://'):
            raise ValueError('Identity Domain discovery has no HTTPS issuer.')
        m = self.oci.identity_domains.models
        client = self.oci.identity_domains.IdentityDomainsClient(self.config, signer=self.signer, service_endpoint=domain)
        base = self.j['applicationURL']
        app = m.App(schemas=['urn:ietf:params:scim:schemas:oracle:idcs:App'], display_name=self.j['name'], active=True,
            is_o_auth_client=True, client_type='confidential', login_mechanism='OIDC',
            allowed_grants=['authorization_code'],
            redirect_uris=[base+'/auth/callback'], post_logout_redirect_uris=[base+'/'],
            based_on_template=m.AppBasedOnTemplate(value='CustomWebAppTemplateId', well_known_id='CustomWebAppTemplateId'))
        self.j['operations']['sso'] = {'started': time.time()}; self.save()
        result = client.create_app(app=app, retry_strategy=self.oci.retry.NoneRetryStrategy()).data
        self.j['operations']['sso']['id'] = result.id
        self.j['sso'] = {'clientId': result.name, 'issuer': discovery['issuer'],
                         'domain': domain.rstrip('/'), 'discoveryURL': discovery_url}; self.save()
        if not result.client_secret: raise ValueError('SSO created without a returned secret. Use the domain credential process and seed SSO_CLIENT_SECRET separately.')
        self.seed('SSO_CLIENT_SECRET', result.client_secret)
        return result.id

    def environment(self, openai_project):
        if set(SECRET_NAMES) - self.j['secrets'].keys(): raise ValueError('Seed all five dedicated application credentials first.')
        if not openai_project: raise ValueError('Supply the actual OpenAI project ID.')
        url = urlsplit(self.j['applicationURL']); o = self.j['outputs']
        env = dict(APP_ENV='hosted', APP_HOST='0.0.0.0', APP_ORIGIN=f'{url.scheme}://{url.netloc}',
            APP_BASE_PATH=url.path, PORTFOLIO_REHEARSAL='0', OCI_AUTH_MODE='resource_principal', OCI_REGION=o['region'],
            OCI_SANDBOX_REGION=o['region'], OCI_SANDBOX_PROJECT_ID=self.sandbox_project_id(),
            OCI_NAMESPACE=o['namespace'], OCI_OBJECT_BUCKET=o['bucket'], OPENAI_PROJECT_ID=openai_project,
            OPENAI_MODEL=self.j['model'], SSO_ISSUER_URL=self.j['sso']['issuer'], SSO_CLIENT_ID=self.j['sso']['clientId'])
        if self.j['sso'].get('discoveryURL'):
            env['SSO_DISCOVERY_URL'] = self.j['sso']['discoveryURL']
        bootstrap = self.j.get('access_bootstrap', {})
        if bootstrap.get('runId'):
            env['AUTH_BOOTSTRAP_ADMIN_RUN_ID'] = bootstrap['runId']
        elif bootstrap.get('email'):
            env['AUTH_BOOTSTRAP_ADMIN_EMAIL'] = bootstrap['email']
        platform = APP/'deploy/private/openai-resources.json'
        if platform.exists():
            created = json.loads(platform.read_text())
            if created['projectId'] != openai_project: raise ValueError('OpenAI project differs from the saved platform resources.')
            env.update(OPENAI_AGENT_ID=created['agentId'], OPENAI_BOOTSTRAP_SESSION_ID=created['sessionId'])
        env.update({name: json.dumps({'secretId': self.j['secrets'][name]}, separators=(',', ':')) for name in SECRET_NAMES})
        private_json(self.path.parent/'hosted-env.json', env)
        # Root .env is for local development: keep security-token profile authentication.
        local = {**env, 'APP_ENV':'local', 'APP_HOST':'127.0.0.1', 'APP_ORIGIN':'http://127.0.0.1:8780',
                 'APP_BASE_PATH':'', 'OCI_AUTH_MODE':'security_token', 'OCI_PROFILE':'Sandbox'}
        path = APP.parent/'.env'
        existing = read_env(path)
        temporary = path.with_name('.env.hosted-pending')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd,'w') as f:
            for name,value in {**existing, **local}.items(): f.write(name+'='+json.dumps(value)+'\n' if '\n' in value else name+'='+"'"+value+"'\n")
        os.replace(temporary,path)
        path.chmod(0o600)
        return 'Non-secret environment references generated.'

    def configure(self):
        m = self.oci.generative_ai.models
        env = json.loads((self.path.parent/'hosted-env.json').read_text())
        for name in SECRET_NAMES:
            if json.loads(env[name]) != {'secretId':self.j['secrets'][name]}: raise ValueError('Unexpected secret reference.')
        aid = self.j['operations']['application']['id']
        response = self.client.get_hosted_application(aid)
        self.operation('configure:'+hashlib.sha256(json.dumps(env,sort_keys=True).encode()).hexdigest()[:16],
            lambda token: self.client.update_hosted_application(aid, m.UpdateHostedApplicationDetails(
                environment_variables=[m.EnvironmentVariable(name=k,type='PLAINTEXT',value=v) for k,v in env.items()]),
                if_match=response.headers['etag'], opc_request_id=token,
                retry_strategy=self.oci.retry.NoneRetryStrategy()), lambda response: aid)
        return aid

    def deploy(self, container_uri, tag, digest, retry_failed=False, activation_attempt=1):
        if activation_attempt < 1 or (activation_attempt > 1 and not retry_failed):
            raise ValueError('Additional activation attempts require an explicit failed-artifact retry.')
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest or ''): raise ValueError('Supply the reviewed image digest.')
        if not container_uri or not tag: raise ValueError('Supply the immutable OCIR repository URI and tag.')
        expected_uri = self.j['outputs'].get('registry_host','')+'/'+self.j['outputs']['namespace']+'/'+self.j['outputs'].get('repository_name','')
        if container_uri != expected_uri: raise ValueError('Image repository URI differs from the approved dedicated registry.')
        # Confirm the tag points at the reviewed digest, using OCIR metadata before activation.
        images = self.oci.pagination.list_call_get_all_results(self.oci.artifacts.ArtifactsClient(self.config, signer=self.signer).list_container_images,
            compartment_id=self.j['outputs']['compartment_id'], repository_id=self.j['outputs']['repository_id']).data
        images = images.items if hasattr(images,'items') and not isinstance(images,list) else images
        if not any(x.version == tag and x.digest == digest for x in images): raise ValueError('OCIR tag/digest could not be verified.')
        m = self.oci.generative_ai.models
        if self.j.get('expectedImage', {}).get('digest') != digest:
            existing_id = self.j['operations'].get('deployment', {}).get('id')
            previous = self.client.get_hosted_deployment(existing_id).data.active_artifact if existing_id else None
            self.j['previousArtifact'] = self.oci.util.to_dict(previous) if previous else None
        self.j['expectedImage'] = {'containerUri':container_uri,'tag':tag,'digest':digest}; self.save()
        did = self.prepare_deployment(container_uri, tag)
        response = self.client.get_hosted_deployment(did)
        if response.data.lifecycle_state in ('CREATING', 'UPDATING'):
            raise ValueError('Deployment creation or activation is pending. Inspect its work request before repeating deploy.')
        matches = [a for a in response.data.artifacts or [] if a.container_uri == container_uri and a.tag == tag]
        active = response.data.active_artifact
        if not matches and active and active.container_uri == container_uri and active.tag == tag:
            matches = [active]
        if not matches:
            self.operation('artifact:'+digest, lambda token: self.client.add_artifact(m.AddArtifactDetails(
                artifact=m.CreateSingleDockerArtifactDetails(artifact_type='SIMPLE_DOCKER_ARTIFACT', container_uri=container_uri,
                    tag=tag, is_vulnerability_scan_required=True)), did, opc_retry_token=token), lambda _: 'accepted')
            response = self.client.get_hosted_deployment(did)
            matches = [a for a in response.data.artifacts or [] if a.container_uri == container_uri and a.tag == tag]
        if len(matches) == 1 and matches[0].status == 'FAILED' and retry_failed:
            previous_key = ('activate:'+digest + (':'+str(activation_attempt-1) if activation_attempt > 2 else ''))
            previous = self.j['operations'].get(previous_key, {}) if activation_attempt > 1 else self.j['operations']['deployment']
            work_request = previous.get('workRequest')
            if (response.data.lifecycle_state != 'NEEDS_ATTENTION' or not work_request or
                    self.client.get_work_request(work_request).data.status != 'FAILED'):
                raise ValueError('Reconcile the terminal failed deployment work request before retrying its artifact.')
        elif len(matches) == 1 and matches[0].status == 'FAILED':
            raise ValueError('Artifact activation failed. Inspect the saved work request, correct its cause, then use --retry-failed for the same image.')
        elif len(matches) != 1 or matches[0].status not in ('INACTIVE','ACTIVE'):
            raise ValueError('Artifact registration or scanning is pending. Inspect status then repeat deploy with the same image; it will not add another artifact.')
        artifact_id = matches[0].id
        active = response.data.active_artifact
        self.j['expectedImage'] = {'containerUri':container_uri,'tag':tag,'digest':digest,'artifactId':artifact_id}; self.save()
        if active and active.id == artifact_id:
            return 'The requested artifact is selected; verify deployment state and digest with status.'
        application = self.client.get_hosted_application(self.j['operations']['application']['id']).data
        if application.lifecycle_state != 'ACTIVE':
            raise ValueError('Application configuration is pending. Wait for ACTIVE before activating the saved artifact.')
        activation_key = 'activate:'+digest + (':'+str(activation_attempt) if activation_attempt > 1 else '')
        self.operation(activation_key, lambda token: self.client.update_hosted_deployment(did,
            m.UpdateHostedDeploymentDetails(active_artifact=m.SingleDockerArtifact(id=artifact_id, artifact_type='SIMPLE_DOCKER_ARTIFACT',
                container_uri=container_uri, tag=tag)), if_match=response.headers['etag'], opc_request_id=token,
                retry_strategy=self.oci.retry.NoneRetryStrategy()), lambda _: artifact_id)
        return 'Activation requested; use status to verify completion and image identity.'

    def status(self):
        result = {'deploymentId':self.j['id'],'operations':{k:v.get('id','UNCONFIRMED') for k,v in self.j['operations'].items()}}
        did = self.j['operations'].get('deployment',{}).get('id')
        if did:
            deployment = self.client.get_hosted_deployment(did).data
            expected, actual = self.j.get('expectedImage',{}), deployment.active_artifact
            result['deploymentState'] = deployment.lifecycle_state
            result['activeArtifactMatches'] = bool(actual and actual.id == expected.get('artifactId') and actual.container_uri == expected.get('containerUri') and actual.tag == expected.get('tag'))
            images = self.oci.pagination.list_call_get_all_results(self.oci.artifacts.ArtifactsClient(self.config, signer=self.signer).list_container_images,
                compartment_id=self.j['outputs']['compartment_id'], repository_id=self.j['outputs']['repository_id']).data
            images = images.items if hasattr(images,'items') and not isinstance(images,list) else images
            result['registryDigestMatches'] = any(x.version == expected.get('tag') and x.digest == expected.get('digest') for x in images)
            result['verifiedDigest'] = expected.get('digest') if result['activeArtifactMatches'] and result['registryDigestMatches'] and deployment.lifecycle_state == 'ACTIVE' and actual.status == 'ACTIVE' else None
        return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['prepare','prepare-vault','vault-foundation','sync-references','seed-openai','import-app-key','import-executor-key','reclassify-executor-key','project','select-sandbox-project','adopt-foundation','application','seed','sso','environment','configure','deploy','status'])
    p.add_argument('--journal',type=Path,default=APP/'deploy/private/rollout.json')
    p.add_argument('--outputs',type=Path);p.add_argument('--profile',default='Sandbox');p.add_argument('--review-id')
    p.add_argument('--name',default='portfolio-advisor');p.add_argument('--model',default='gpt-5.6-luna')
    p.add_argument('--variable',choices=[*SECRET_NAMES,'POSTGRES_ADMIN_PASSWORD']);p.add_argument('--domain')
    p.add_argument('--openai-project');p.add_argument('--container-uri');p.add_argument('--tag');p.add_argument('--digest')
    p.add_argument('--retry-failed', action='store_true', help='Retry the same artifact after correcting and reconciling a terminal failed initial deployment.')
    p.add_argument('--activation-attempt', type=int, default=1, help='Explicit retry number; each earlier activation work request must be reconciled as FAILED.')
    p.add_argument('--compartment');p.add_argument('--region',default='us-chicago-1')
    p.add_argument('--sandbox-project');p.add_argument('--sandbox');p.add_argument('--command-id')
    a=p.parse_args()
    if a.command in ('prepare','prepare-vault'):
        if a.journal.exists(): raise ValueError('Journal already exists; refusing replacement.')
        if a.command=='prepare':
            if not a.outputs: raise ValueError('Supply terraform output -json rollout saved to a file.')
            outputs=json.loads(a.outputs.read_text())
        else:
            if not a.compartment or not a.compartment.startswith('ocid1.compartment.'): raise ValueError('Supply the approved compartment OCID.')
            outputs={'compartment_id':a.compartment,'region':a.region}
        private_json(a.journal, {'id':str(uuid.uuid4()),'name':a.name,'model':a.model,'outputs':outputs,'operations':{},'secrets':{}})
        print('Prepared local rollout journal; no cloud actions taken.');return
    check()
    if a.command!='status' and not a.review_id: raise ValueError('Provide the separate approved rollout review ID before mutations.')
    r=Rollout(a.journal,a.profile)
    if a.review_id:
        r.j.setdefault('reviews',[]).append({'id':a.review_id,'operation':a.command,'at':time.time()});r.save()
    if a.command=='seed': result=r.seed(a.variable)
    elif a.command=='sso':result=r.sso(a.domain)
    elif a.command=='environment':result=r.environment(a.openai_project)
    elif a.command=='select-sandbox-project':result=r.select_sandbox_project(a.sandbox_project,a.sandbox,a.command_id)
    elif a.command=='deploy':result=r.deploy(a.container_uri,a.tag,a.digest,a.retry_failed,a.activation_attempt)
    else:result=getattr(r,a.command.replace('-','_'))()
    print(json.dumps(result,indent=2))

if __name__=='__main__':
    try: main()
    except ValueError as exc: raise SystemExit(str(exc)) from None
    except Exception: raise SystemExit('OCI operation failed. Retained journal IDs and retry tokens require reconciliation; credentials and provider response bodies are suppressed.') from None
