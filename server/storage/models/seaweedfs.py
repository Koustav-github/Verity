import os

import boto3


class SeaweedFSStore:
    def __init__(self, bucket: str, endpoint_url: str, client=None):
        self.bucket = bucket
        self.client = client or boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=os.environ.get("SEAWEEDFS_ACCESS_KEY", "verity"),
            aws_secret_access_key=os.environ.get("SEAWEEDFS_SECRET_KEY", "verity"),
        )

    def put(self, sha256: str, payload: bytes) -> str:
        self.client.put_object(Bucket=self.bucket, Key=sha256, Body=payload)
        return f"seaweedfs://{self.bucket}/{sha256}"
