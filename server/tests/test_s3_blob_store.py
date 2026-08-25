import boto3
import pytest

from storage.models.s3 import S3BlobStore


class FakeS3Client:
    def __init__(self):
        self.calls = []

    def put_object(self, Bucket, Key, Body):
        self.calls.append({"Bucket": Bucket, "Key": Key, "Body": Body})


def test_put_stores_bytes_under_content_addressed_key_and_returns_a_uri():
    fake_client = FakeS3Client()
    store = S3BlobStore(bucket="verity-artifacts", region="us-east-1", client=fake_client)

    uri = store.put("abc123", b"fake-artifact-bytes")

    assert fake_client.calls == [
        {"Bucket": "verity-artifacts", "Key": "abc123", "Body": b"fake-artifact-bytes"}
    ]
    assert uri == "s3://verity-artifacts/abc123"


def test_default_client_targets_the_configured_region(monkeypatch):
    captured = {}

    def fake_boto3_client(service, **kwargs):
        captured["service"] = service
        captured["kwargs"] = kwargs
        return "the-boto3-client"

    monkeypatch.setattr(boto3, "client", fake_boto3_client)

    store = S3BlobStore(bucket="verity-artifacts", region="us-east-1")

    assert captured["service"] == "s3"
    assert captured["kwargs"]["region_name"] == "us-east-1"
    assert store.client == "the-boto3-client"


def test_no_endpoint_url_is_sent_when_targeting_real_aws(monkeypatch):
    captured = {}
    monkeypatch.setattr(boto3, "client", lambda service, **kwargs: captured.update(kwargs))
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)

    S3BlobStore(bucket="verity-artifacts", region="us-east-1")

    # Passing endpoint_url=None to boto3 is not the same as omitting it; AWS resolves
    # the endpoint from the region on its own.
    assert "endpoint_url" not in captured


def test_an_endpoint_override_is_honoured_so_r2_or_minio_stay_one_env_var_away(monkeypatch):
    captured = {}
    monkeypatch.setattr(boto3, "client", lambda service, **kwargs: captured.update(kwargs))
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://account.r2.cloudflarestorage.com")

    S3BlobStore(bucket="verity-artifacts", region="auto")

    assert captured["endpoint_url"] == "https://account.r2.cloudflarestorage.com"


def test_credentials_are_left_to_the_boto3_chain_rather_than_defaulted(monkeypatch):
    """The old SeaweedFS store fell back to "verity"/"verity" when env vars were
    missing. Against real AWS that would silently send junk credentials and fail
    confusingly; boto3's own chain reports missing credentials clearly instead."""
    captured = {}
    monkeypatch.setattr(boto3, "client", lambda service, **kwargs: captured.update(kwargs))

    S3BlobStore(bucket="verity-artifacts", region="us-east-1")

    assert "aws_access_key_id" not in captured
    assert "aws_secret_access_key" not in captured


class FakeS3ClientWithPresign(FakeS3Client):
    def __init__(self):
        super().__init__()
        self.presign_calls = []

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.presign_calls.append(
            {"operation": operation, "Params": Params, "ExpiresIn": ExpiresIn}
        )
        return f"https://{Params['Bucket']}.s3.amazonaws.com/{Params['Key']}?X-Amz-Expires={ExpiresIn}"


def test_presigned_url_requests_a_get_object_url_for_the_sha256_key():
    fake_client = FakeS3ClientWithPresign()
    store = S3BlobStore(bucket="verity-artifacts", region="us-east-1", client=fake_client)

    url = store.presigned_url("abc123")

    assert fake_client.presign_calls == [
        {
            "operation": "get_object",
            "Params": {"Bucket": "verity-artifacts", "Key": "abc123"},
            "ExpiresIn": 900,
        }
    ]
    assert url == "https://verity-artifacts.s3.amazonaws.com/abc123?X-Amz-Expires=900"


def test_presigned_url_accepts_a_custom_expiry():
    fake_client = FakeS3ClientWithPresign()
    store = S3BlobStore(bucket="verity-artifacts", region="us-east-1", client=fake_client)

    store.presigned_url("abc123", expires_in=60)

    assert fake_client.presign_calls[0]["ExpiresIn"] == 60
