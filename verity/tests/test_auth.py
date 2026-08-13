from verity.auth import sign_in, sign_up


class FakeSession:
    access_token = "fake-access-token"


class FakeUser:
    id = "user-uuid-123"


class FakeAuthResponse:
    session = FakeSession()
    user = FakeUser()


class FakeAuth:
    def __init__(self):
        self.calls = []

    def sign_up(self, credentials):
        self.calls.append(("sign_up", credentials))
        return FakeAuthResponse()

    def sign_in_with_password(self, credentials):
        self.calls.append(("sign_in_with_password", credentials))
        return FakeAuthResponse()


class FakeSupabaseClient:
    def __init__(self):
        self.auth = FakeAuth()


def test_sign_up_returns_access_token_and_user_id():
    client = FakeSupabaseClient()

    result = sign_up("a@example.com", "hunter2", client=client)

    assert client.auth.calls == [
        ("sign_up", {"email": "a@example.com", "password": "hunter2"})
    ]
    assert result == {"access_token": "fake-access-token", "user_id": "user-uuid-123"}


def test_sign_in_returns_access_token_and_user_id():
    client = FakeSupabaseClient()

    result = sign_in("a@example.com", "hunter2", client=client)

    assert client.auth.calls == [
        ("sign_in_with_password", {"email": "a@example.com", "password": "hunter2"})
    ]
    assert result == {"access_token": "fake-access-token", "user_id": "user-uuid-123"}
