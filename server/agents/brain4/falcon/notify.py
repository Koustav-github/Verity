"""Falcon's notification: an in-app row first, an email best-effort second.

The row is the source of truth for whether an alert fired at all. Email delivery can
fail — a dead SES endpoint, an unconfigured account — without that failure erasing the
alert; it just arrives without an email a human happened to see.
"""

from agents.brain4.falcon.detect import WINDOW_MINUTES

# Reuses detect.py's own 15-minute cadence rather than inventing a second, unrelated
# time constant: a model with a persisting regression, checked every few seconds by the
# background telemetry flush, must produce one alert (and one email) per incident, not
# one per check. WINDOW_MINUTES is exactly the recent-window size detect.py already
# uses for its own comparisons, so this cooldown fires on the same cadence the anomaly
# checks themselves operate on.
ALERT_COOLDOWN_MINUTES = WINDOW_MINUTES


def record_and_notify(
    *, model_version_id, kind, metric, detail, metadata_store, email_fn=None, now_fn=None
):
    email_fn = email_fn or _default_email_fn
    now_fn = now_fn or _default_now

    if _in_cooldown(metadata_store=metadata_store, model_version_id=model_version_id, kind=kind, now=now_fn()):
        return None

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


def _in_cooldown(*, metadata_store, model_version_id, kind, now):
    """True if the most recent alert_event for this exact (model_version_id, kind) pair
    landed within ALERT_COOLDOWN_MINUTES of `now`. find_alert_events isn't filtered by
    kind at the store layer, so that filtering happens here in Python; systemic and
    quality alerts must never suppress each other."""
    from datetime import timedelta

    existing = metadata_store.find_alert_events(model_version_id=model_version_id)
    same_kind = [a for a in existing if a["kind"] == kind]
    if not same_kind:
        return False

    # find_alert_events already orders newest-first; filtering preserves that order.
    most_recent = same_kind[0]
    return now - _created_at(most_recent) < timedelta(minutes=ALERT_COOLDOWN_MINUTES)


def _created_at(alert):
    """Parse an alert_event's created_at back into a comparable datetime — the same
    shape monitor.py's _occurred_at() parses for telemetry_event.occurred_at. Handles a
    trailing "Z" the same way, so a real Postgres timestamp and a test fixture built
    with isoformat() compare correctly either way."""
    from datetime import datetime

    value = alert["created_at"]
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def _find_model(metadata_store, model_version_id):
    try:
        return metadata_store.find_model_by_version(model_version_id=model_version_id)
    except Exception:  # noqa: BLE001
        return None


def _now_iso():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _default_now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _default_email_fn(**kwargs):
    from agents.brain4.falcon.email import send_alert_email

    return send_alert_email(**kwargs)
