"""Web search types and calls."""

from dataclasses import dataclass


@dataclass(frozen=True)
class WebPage:
    url: str
    title: str
    text: str
