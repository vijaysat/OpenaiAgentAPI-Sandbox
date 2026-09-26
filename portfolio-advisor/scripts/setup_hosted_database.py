"""Create the restricted app database over a temporary OCI Bastion tunnel.

Passwords stay in Vault and process memory. The build and runtime never receive
the administrator password. The tunnel session is limited to one database port.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import sys
import time
from urllib.parse import quote, urlsplit, unquote

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP), str(APP.parent), str(APP / 'scripts')]
from rollout import Rollout


def secret(r, name):
    client = r.oci.secrets.SecretsClient(r.config, signer=r.signer)
    value = client.get_secret_bundle(r.j['secrets'][name], stage='CURRENT').data.secret_bundle_content.content
    return base64.b64decode(value, validate=True).decode()


def prepare(r):
    o = r.j['foundation_outputs']
    client = r.oci.psql.PostgresqlClient(r.config, signer=r.signer)
    db = client.get_db_system(o['database_id']).data
    if db.lifecycle_state != 'ACTIVE' or db.compartment_id != o['compartment_id']:
        raise ValueError('The dedicated PostgreSQL system must be ACTIVE in its own compartment.')
    details = client.get_connection_details(o['database_id']).data
    if '-----BEGIN CERTIFICATE-----' not in details.ca_certificate:
        raise ValueError('Database service did not return its PEM CA certificate.')
    certificate = APP / 'deploy/certs/postgres-ca.pem'
    certificate.write_text(details.ca_certificate); certificate.chmod(0o644)
    endpoint = details.primary_db_endpoint
    r.j['database_endpoint'] = {'host': endpoint.fqdn, 'address': endpoint.ip_address, 'port': endpoint.port}
    r.save()
    if 'DATABASE_URL' not in r.j['secrets']:
        password = secrets.token_urlsafe(40)
        url = f'postgresql+psycopg://portfolio_app:{quote(password, safe="")}@{endpoint.fqdn}:{endpoint.port}/portfolio_app?sslmode=verify-full&sslrootcert=/app/certs/postgres-ca.pem'
        r.seed('DATABASE_URL', url)
    return {'database_id': o['database_id'], 'certificate_saved': True, 'credential_saved_in_vault': True}


def create_session(r):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    bastion_id = r.j['foundation_outputs'].get('database_setup_bastion_id')
    if not bastion_id: raise ValueError('Apply the temporary database setup Terraform first.')
    keyfile = APP / 'deploy/private/db-setup-key'
    if not keyfile.exists():
        if r.j['operations'].get('database-setup-session'):
            raise ValueError('A setup session exists without its key; reconcile it before continuing.')
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                      serialization.NoEncryption()))
    key = serialization.load_pem_private_key(keyfile.read_bytes(), password=None)
    public = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
    client = r.oci.bastion.BastionClient(r.config, signer=r.signer, retry_strategy=r.oci.retry.NoneRetryStrategy())
    m = r.oci.bastion.models; endpoint = r.j['database_endpoint']
    sid = r.operation('database-setup-session', lambda token: client.create_session(m.CreateSessionDetails(
        display_name='portfolio-advisor-database-setup', bastion_id=bastion_id,
        target_resource_details=m.CreatePortForwardingSessionTargetResourceDetails(
            target_resource_private_ip_address=endpoint['address'], target_resource_port=endpoint['port']),
        key_type='PUB', key_details=m.PublicKeyDetails(public_key_content=public), session_ttl_in_seconds=1800),
        opc_retry_token=token))
    return {'session_id': sid, 'state': client.get_session(sid).data.lifecycle_state}


def provision_database(connection, password):
    from psycopg import sql
    if not connection.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', ('portfolio_app',)).fetchone():
        connection.execute(sql.SQL('CREATE ROLE portfolio_app LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION').format(sql.Literal(password)))
    flags = connection.execute('SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication FROM pg_roles WHERE rolname=%s', ('portfolio_app',)).fetchone()
    if any(flags): raise ValueError('The runtime role has unexpected elevated privileges; setup stopped.')
    owner = connection.execute('SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s', ('portfolio_app',)).fetchone()
    if owner and owner[0] != 'portfolio_app':
        raise ValueError('The database belongs to another role; setup stopped.')
    if not owner:
        # PostgreSQL 16 gives the role creator ADMIN but not SET by default.
        # Only the administrator receives membership; the app gains no privilege.
        connection.execute('GRANT portfolio_app TO CURRENT_USER WITH SET TRUE')
        connection.execute('CREATE DATABASE portfolio_app OWNER portfolio_app')
    connection.execute('REVOKE ALL ON DATABASE portfolio_app FROM PUBLIC')


def bootstrap(r):
    import psycopg
    sid = r.j['operations']['database-setup-session']['id']
    client = r.oci.bastion.BastionClient(r.config, signer=r.signer)
    session = client.get_session(sid).data
    if session.lifecycle_state != 'ACTIVE': raise ValueError('The temporary Bastion session is not ACTIVE yet.')
    destinations = [part for command in (session.ssh_metadata or {}).values() for part in shlex.split(command)
                    if part.startswith(sid + '@')]
    if len(set(destinations)) != 1 or not destinations[0].endswith('.oci.oraclecloud.com'):
        raise ValueError('Bastion did not return an expected OCI SSH destination.')
    endpoint = r.j['database_endpoint']; keyfile = APP / 'deploy/private/db-setup-key'
    local_port = 55440
    args = ['ssh', '-i', str(keyfile), '-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes',
            '-o', 'StrictHostKeyChecking=accept-new', '-o', 'UserKnownHostsFile=' + str(APP / 'deploy/private/bastion-known-hosts'),
            '-L', f'127.0.0.1:{local_port}:{endpoint["address"]}:{endpoint["port"]}', destinations[0]]
    tunnel = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        app_url = urlsplit(secret(r, 'DATABASE_URL'))
        if app_url.hostname != endpoint['host'] or app_url.username != 'portfolio_app' or app_url.path != '/portfolio_app':
            raise ValueError('Runtime Vault connection does not match the dedicated database.')
        credentials = dict(host=endpoint['host'], hostaddr='127.0.0.1', port=local_port,
                           sslmode='verify-full', sslrootcert=str(APP / 'deploy/certs/postgres-ca.pem'), connect_timeout=5)
        admin_password = secret(r, 'POSTGRES_ADMIN_PASSWORD'); deadline = time.monotonic() + 60
        while True:
            if tunnel.poll() is not None: raise ValueError('The setup SSH tunnel did not start; check the client-IP rule and Bastion session.')
            try:
                connection = psycopg.connect(**credentials, dbname='postgres', user='portfolio_admin',
                                             password=admin_password, autocommit=True)
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline: raise ValueError('Private PostgreSQL connection could not be verified; provider details are suppressed.') from None
                time.sleep(2)
        r.j.setdefault('database_bootstrap', {})['started_at'] = time.time(); r.save()
        with connection: provision_database(connection, unquote(app_url.password))
        with psycopg.connect(**credentials, dbname='portfolio_app', user='portfolio_app', password=unquote(app_url.password)) as app:
            assert app.execute('SELECT current_user').fetchone()[0] == 'portfolio_app'
            app.execute('CREATE TEMP TABLE portfolio_permission_probe (value integer)')
            app.execute('INSERT INTO portfolio_permission_probe VALUES (1)')
            assert app.execute('SELECT value FROM portfolio_permission_probe').fetchone()[0] == 1
        r.j['database_bootstrap'].update(verified_at=time.time(), restricted_role=True, tls='verify-full'); r.save()
        return {'database_ready': True, 'runtime_role': 'portfolio_app', 'tls': 'verify-full'}
    finally:
        tunnel.terminate()
        try: tunnel.wait(timeout=10)
        except subprocess.TimeoutExpired: tunnel.kill(); tunnel.wait()


def cleanup(r):
    sid = r.j['operations']['database-setup-session']['id']
    client = r.oci.bastion.BastionClient(r.config, signer=r.signer, retry_strategy=r.oci.retry.NoneRetryStrategy())
    r.operation('delete-database-setup-session', lambda _: client.delete_session(sid), lambda _: sid)
    try: state = client.get_session(sid).data.lifecycle_state
    except r.oci.exceptions.ServiceError as exc:
        if exc.status != 404: raise
        state = 'DELETED'
    if state == 'DELETED':
        (APP / 'deploy/private/db-setup-key').unlink(missing_ok=True)
        r.j['database_bootstrap']['session_cleanup_confirmed_at'] = time.time(); r.save()
    return {'session_state': state, 'next': 'Disable database_setup_client_cidr in Terraform after confirmed deletion.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['prepare', 'session', 'bootstrap', 'cleanup'])
    p.add_argument('--review-id', required=True)
    args = p.parse_args(); r = Rollout(APP / 'deploy/private/vault-bootstrap.json', 'Sandbox')
    result = {'prepare': prepare, 'session': create_session, 'bootstrap': bootstrap, 'cleanup': cleanup}[args.action](r)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try: main()
    except ValueError as exc: raise SystemExit(str(exc)) from None
    except Exception as exc:
        raise SystemExit(f'Database setup failed ({type(exc).__name__}, HTTP {getattr(exc, "status", None)}); '
                         'credentials and provider bodies are suppressed.') from None
