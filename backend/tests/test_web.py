import pytest
import requests
from tavily import errors

from ebb.config import Settings
from ebb.web import TavilySearch, WebPage, WebSearchFailed, web_search


class FakeClient:
    """Stands in for tavily.TavilyClient: records the call, returns or raises the canned value."""

    outcome: object = {}
    calls: list = []

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def search(self, query: str, **options):
        type(self).calls.append((query, options))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


@pytest.fixture
def client(monkeypatch):
    FakeClient.calls = []
    monkeypatch.setattr("tavily.TavilyClient", FakeClient)
    return FakeClient


def test_tavily_results_become_pages(client):
    client.outcome = {
        "results": [
            {"url": "https://a", "title": "A", "raw_content": "r" * 5000, "content": "short"},
            {"url": "https://b", "title": "B", "raw_content": None, "content": "snippet"},
            {"url": "https://c", "title": "C", "raw_content": "", "content": ""},
            {"url": "https://d", "title": "D", "raw_content": "  ", "content": None},
        ]
    }
    pages = TavilySearch("key").search("dns ttl")
    assert pages == [WebPage("https://a", "A", "r" * 4000), WebPage("https://b", "B", "snippet")]
    assert client.calls == [
        ("dns ttl", {"search_depth": "basic", "max_results": 5, "include_raw_content": "text"})
    ]


@pytest.mark.parametrize(
    "error",
    [
        errors.UsageLimitExceededError("limit"),
        errors.InvalidAPIKeyError("bad key"),
        errors.ForbiddenError("no"),
        errors.BadRequestError("bad"),
        errors.TimeoutError(60),
        requests.exceptions.ConnectionError("down"),
        requests.exceptions.HTTPError("500"),
    ],
)
def test_tavily_errors_become_web_search_failed(client, error):
    client.outcome = error
    with pytest.raises(WebSearchFailed) as caught:
        TavilySearch("key").search("q")
    assert caught.value.__cause__ is error


def test_other_errors_are_not_swallowed(client):
    client.outcome = KeyError("bug")
    with pytest.raises(KeyError):
        TavilySearch("key").search("q")


def test_no_key_means_no_web_search():
    assert web_search(Settings(_env_file=None, tavily_api_key=None)) is None
    assert isinstance(web_search(Settings(_env_file=None, tavily_api_key="k")), TavilySearch)
