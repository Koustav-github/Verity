import os

import boto3


class S3BlobStore:
    """Content-addressed blob storage over the S3 API.

    Targets real AWS by default. Set S3_ENDPOINT_URL to point at any other
    S3-compatible service — R2, MinIO, a local gateway — without touching code.

    Credentials come from boto3's standard chain (AWS_ACCESS_KEY_ID /
    AWS_SECRET_ACCESS_KEY, shared config, or an instance role). There is
    deliberately no fallback default: junk credentials fail in confusing ways,
    whereas a missing-credentials error from boto3 names the problem.
    """

    def __init__(self, bucket: str, region: str, client=None):
        self.bucket = bucket
        if client is not None:
            self.client = client
            return

        kwargs = {"region_name": region}
        endpoint_url = os.environ.get("S3_ENDPOINT_URL")
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        self.client = boto3.client("s3", **kwargs)

    def put(self, sha256: str, payload: bytes) -> str:
        self.client.put_object(Bucket=self.bucket, Key=sha256, Body=payload)
        return f"s3://{self.bucket}/{sha256}"

    def presigned_url(self, sha256: str, expires_in: int = 900) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": sha256},
            ExpiresIn=expires_in,
        )
