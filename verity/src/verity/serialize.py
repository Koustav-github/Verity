import hashlib
import cloudpickle

def serialize(model) -> tuple[bytes, str]:
    payload = cloudpickle.dumps(model)
    digest = hashlib.sha256(payload).hexdigest()
    return payload, digest