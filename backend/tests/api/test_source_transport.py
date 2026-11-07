"""No network access: the transport receives an artificial HTTPS connection."""

from io import BytesIO

import pytest

from app.api.errors import ApiError
from app.services import source_http


@pytest.mark.parametrize(
    ("status", "declared", "body", "accepted"),
    [
        (200, "3", b"abc", True),
        (307, None, b"redirect-body", True),
        (200, "200", b"", False),
        (200, None, b"four", False),
        (200, "3", b"x", False),
    ],
)
def test_fetch_limits_and_no_redirect(monkeypatch, status, declared, body, accepted):
    called = []
    stream = BytesIO(body)

    class Connection:
        def __init__(self, hostname, port, timeout):
            assert (hostname, port) == ("source.invalid", None)

        def request(self, method, path, headers):
            called.append((method, path))

        def getresponse(self):
            return self

        def getheader(self, name, default=None):
            return declared if name == "Content-Length" else default

        def getheaders(self):
            return []

        def read1(self, size):
            return stream.read(size)

        def close(self):
            stream.close()

    Connection.status = status
    monkeypatch.setattr(source_http.http.client, "HTTPSConnection", Connection)
    url = "https://source.invalid/test?example=synthetic"
    if accepted:
        result = source_http.fetch(url, max_bytes=3)
        assert result == (status, {}, body if status == 200 else b"")
    else:
        with pytest.raises(ApiError) as caught:
            source_http.fetch(url, max_bytes=3)
        assert caught.value.status_code == 502
        assert url not in str(caught.value)
    assert called == [("GET", "/test?example=synthetic")]
    assert stream.closed
