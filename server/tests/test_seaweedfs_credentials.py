import boto3

from storage.models.seaweedfs import SeaweedFSStore


def test_default_client_is_configured_with_credentials(monkeypatch):
    captured = {}

    def fake_boto3_client(service, **kwargs):
        captured["service"] = service
        captured["kwargs"] = kwargs
        return "the-boto3-client"

    monkeypatch.setattr(boto3, "client", fake_boto3_client)
    monkeypatch.setenv("SEAWEEDFS_ACCESS_KEY", "test-access")
    monkeypatch.setenv("SEAWEEDFS_SECRET_KEY", "test-secret")

    store = SeaweedFSStore(bucket="verity-artifacts", endpoint_url="http://seaweedfs.test:8333")

    assert captured["service"] == "s3"
    assert captured["kwargs"]["endpoint_url"] == "http://seaweedfs.test:8333"
    assert captured["kwargs"]["aws_access_key_id"] == "test-access"
    assert captured["kwargs"]["aws_secret_access_key"] == "test-secret"
    assert store.client == "the-boto3-client"
