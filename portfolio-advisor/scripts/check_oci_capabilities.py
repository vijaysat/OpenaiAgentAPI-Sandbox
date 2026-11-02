"""Offline SDK/CLI capability check; never authenticates or calls OCI."""
import json

def check():
    import oci
    from oci.generative_ai import GenerativeAiClient, models
    from oci.generative_ai_sandbox import GenerativeAiSandboxClient
    required = {
        GenerativeAiClient: ['create_generative_ai_project', 'get_generative_ai_project', 'create_hosted_application',
                            'get_hosted_application', 'update_hosted_application', 'create_hosted_deployment',
                            'get_hosted_deployment', 'update_hosted_deployment', 'add_artifact'],
        GenerativeAiSandboxClient: ['create_sandbox', 'get_sandbox', 'run_sandbox_command', 'get_sandbox_command',
                                   'write_sandbox_file', 'read_sandbox_file', 'delete_sandbox'],
    }
    missing = [f'{cls.__name__}.{method}' for cls, methods in required.items() for method in methods if not hasattr(cls, method)]
    for model in ['SandboxConfig', 'SandboxAllowNetworkPolicy', 'AllowedDomain', 'CreateHostedApplicationDetails',
                  'CreateHostedDeploymentDetails', 'CreateSingleDockerArtifactDetails', 'SingleDockerArtifact']:
        if not hasattr(models, model): missing.append(model)
    if missing: raise RuntimeError('Required OCI SDK capabilities missing: ' + ', '.join(missing))
    return {'sdkVersion': oci.__version__, 'requiredSdkCapabilities': 'available',
            'deploymentCLI': 'scripts/rollout.py', 'oracleCLI': 'optional; installed separately'}

if __name__ == '__main__':
    try: print(json.dumps(check(), indent=2))
    except Exception: raise SystemExit('OCI SDK capability check failed. Install the public SDK using the application requirements.') from None
