from storage.models.seaweedfs import SeaweedFSStore


class FakeS3Client:
    def __init__(self):
        self.calls = []

    def put_object(self, Bucket, Key, Body):
        self.calls.append({"Bucket": Bucket, "Key": Key, "Body": Body})


def test_put_stores_bytes_under_content_addressed_key_and_returns_a_uri():
    fake_client = FakeS3Client()
    store = SeaweedFSStore(bucket="verity-artifacts", endpoint_url="http://seaweedfs.test:8333", client=fake_client)

    uri = store.put("abc123", b"fake-artifact-bytes")

    assert fake_client.calls == [
        {"Bucket": "verity-artifacts", "Key": "abc123", "Body": b"fake-artifact-bytes"}
    ]
    assert uri == "seaweedfs://verity-artifacts/abc123"
