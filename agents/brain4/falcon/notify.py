"""Falcon's notification: an in-app row first, an email best-effort second.

The row is the source of truth for whether an alert fired at all. Email delivery can
fail — a dead SES endpoint, an unconfigured account — without that failure erasing the
alert; it just arrives without an email a human happened to see.
"""


def record_and_notify(*, model_version_id, kind, metric, detail, metadata_store, email_fn=None):
    email_fn = email_fn or _default_email_fn

    alert_id = metadata_store.save_alert_event(
        model_version_id=model_version_id, kind=kind, metric=metric, detail=detail
    )

    model = _find_model(metadata_store, model_version_id)
    to = (model or {}).get("alert_email")
    if not to:
        return alert_id

    try:
        email_fn(
            to=to,
            subject=f"Verity alert: {kind} — {metric}",
            body=f"model_version_id={model_version_id}\nkind={kind}\nmetric={metric}\ndetail={detail}",
        )
    except Exception:  # noqa: BLE001 - the row already landed; email is best-effort
        return alert_id

    metadata_store.update_alert_event(alert_event_id=alert_id, emailed_at=_now_iso())
    return alert_id


def _find_model(metadata_store, model_version_id):
    try:
        return metadata_store.find_model_by_version(model_version_id=model_version_id)
    except Exception:  # noqa: BLE001
        return None


def _now_iso():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _default_email_fn(**kwargs):
    from agents.brain4.falcon.email import send_alert_email

    return send_alert_email(**kwargs)
