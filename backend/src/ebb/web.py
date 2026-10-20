"""Web search types and calls."""

from dataclasses import dataclass
from typing import Protocol

from ebb.config import Settings

MAX_PAGE_CHARS = 4000


class WebSearchFailed(Exception):
    """The search service or the network failed; there are no results to use."""


@dataclass(frozen=True)
class WebPage:
    url: str
    title: str
    text: str


class WebSearch(Protocol):
    def search(self, query: str) -> list[WebPage]: ...


class TavilySearch:
    def __init__(self, api_key: str) -> None:
        from tavily import TavilyClient  # imported late so CLI start-up stays fast

        self._client = TavilyClient(api_key)

    def search(self, query: str) -> list[WebPage]:
        import requests
        from tavily import errors

        try:
            response = self._client.search(
                query, search_depth="basic", max_results=5, include_raw_content="text"
            )
        except (
            errors.UsageLimitExceededError,
            errors.BadRequestError,
            errors.ForbiddenError,
            errors.InvalidAPIKeyError,
            errors.MissingAPIKeyError,
            errors.TimeoutError,
            requests.exceptions.RequestException,  # tavily-python talks through requests
        ) as error:
            raise WebSearchFailed(str(error) or type(error).__name__) from error
        pages = []
        for result in response.get("results", []):
            text = (result.get("raw_content") or result.get("content") or "").strip()
            if text:
                url = result["url"]
                pages.append(WebPage(url, result.get("title") or url, text[:MAX_PAGE_CHARS]))
        return pages


def web_search(settings: Settings) -> WebSearch | None:
    """The configured search client, or None when no Tavily key is set."""
    if not settings.tavily_api_key:
        return None
    return TavilySearch(settings.tavily_api_key)
