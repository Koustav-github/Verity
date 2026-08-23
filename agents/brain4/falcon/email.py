"""Best-effort alert delivery via AWS SES — the same account api-fication's S3 usage
lives in, so no new vendor. Failure here must never be the reason an alert goes
unrecorded; see notify.py for how that's enforced.
"""

import os


def send_alert_email(*, to, subject, body, client=None):
    client = client or _real_client()
    client.send_email(
        Source=os.environ.get("SES_SENDER", "alerts@verity.dev"),
        Destination={"ToAddresses": [to]},
        Message={
            "Subject": {"Data": subject},
            "Body": {"Text": {"Data": body}},
        },
    )


def _real_client():
    import boto3

    return boto3.client("ses", region_name=os.environ.get("SES_REGION", "us-east-1"))
