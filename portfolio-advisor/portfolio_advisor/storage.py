"""All live artifacts reside in the dedicated private Object Storage bucket."""
import io
from pathlib import Path

from .config import oci_auth


class ObjectStore:
    def __init__(self, settings, client=None):
        self.namespace, self.bucket = settings.get("OCI_NAMESPACE"), settings.get("OCI_OBJECT_BUCKET")
        if client is None:
            import oci
            config, signer = oci_auth(settings.values)
            client = oci.object_storage.ObjectStorageClient(config, signer=signer, timeout=(5, 30))
        self.client = client

    def put(self, key, body, content_type="application/json"):
        self.client.put_object(self.namespace, self.bucket, key, io.BytesIO(body), content_type=content_type)

    def get(self, key):
        return self.client.get_object(self.namespace, self.bucket, key).data.content

    def delete_prefix(self, prefix):
        start = None
        while True:
            page = self.client.list_objects(self.namespace, self.bucket, prefix=prefix, start=start).data
            for obj in page.objects:
                self.client.delete_object(self.namespace, self.bucket, obj.name)
            start = page.next_start_with
            if not start:
                return


class LocalStore:
    """Explicit local rehearsal and test storage only."""
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.folder.mkdir(parents=True, exist_ok=True)

    def path(self, key):
        target = (self.folder / key).resolve()
        if not target.is_relative_to(self.folder):
            raise ValueError("Invalid object key.")
        return target

    def put(self, key, body, content_type="application/json"):
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        path.chmod(0o600)

    def get(self, key):
        return self.path(key).read_bytes()

    def delete_prefix(self, prefix):
        import shutil
        path = self.path(prefix)
        if path.exists():
            shutil.rmtree(path)


def make_store(settings):
    if settings.rehearsal:
        return LocalStore(settings.get("LOCAL_ARTIFACT_DIR", ".local/artifacts"))
    return ObjectStore(settings)
