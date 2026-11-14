"""Confluence Cloud REST client. Authenticates as a service account; per-user read access is
checked with the content permission-check endpoint."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

import httpx


@dataclass(frozen=True)
class WikiHit:
    page_id: str
    title: str
    space_key: str
    url: str
    excerpt: str


@dataclass(frozen=True)
class WikiPage:
    page_id: str
    title: str
    space_key: str
    url: str
    version: int
    text: str
    # (page_id, title) of direct child pages. Index pages often list children with a macro
    # that the rendered body doesn't include, so they're fetched separately.
    children: tuple[tuple[str, str], ...] = ()


class ConfluenceClient:
    def __init__(
        self, base_url: str, email: str, api_token: str, http: httpx.Client | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = http or httpx.Client(timeout=20.0, auth=(email, api_token))

    def _url(self, path: str) -> str:
        return f"{self.base_url}/wiki/rest/api{path}"

    def space_reachable(self, key: str) -> bool:
        resp = self._http.get(self._url(f"/space/{key}"))
        if resp.status_code in (401, 403, 404):
            return False
        resp.raise_for_status()
        return True

    def search(self, query: str, space_keys: list[str], limit: int = 10) -> list[WikiHit]:
        if not space_keys:
            return []
        spaces = ",".join(f'"{_cql_escape(k)}"' for k in space_keys)
        cql = f'type=page AND space.key in ({spaces}) AND text ~ "{_cql_escape(query)}"'
        resp = self._http.get(
            self._url("/search"), params={"cql": cql, "limit": limit, "expand": "content.space"}
        )
        resp.raise_for_status()
        data = resp.json()
        hits = []
        for r in data.get("results", []):
            content = r.get("content") or {}
            if "id" not in content:
                continue
            hits.append(
                WikiHit(
                    page_id=str(content["id"]),
                    title=content.get("title", r.get("title", "")),
                    space_key=(content.get("space") or {}).get("key", ""),
                    url=f"{self.base_url}/wiki{r.get('url', '')}",
                    excerpt=_strip_highlight(r.get("excerpt", "")),
                )
            )
        return hits

    def get_page(self, page_id: str) -> WikiPage:
        resp = self._http.get(
            self._url(f"/content/{page_id}"), params={"expand": "body.view,space,version"}
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return WikiPage(
            page_id=str(data["id"]),
            title=data["title"],
            space_key=data["space"]["key"],
            url=f"{self.base_url}/wiki{data['_links']['webui']}",
            version=int(data["version"]["number"]),
            text=html_to_text(data["body"]["view"]["value"]),
            children=self._children(str(data["id"])),
        )

    def _children(self, page_id: str) -> tuple[tuple[str, str], ...]:
        resp = self._http.get(self._url(f"/content/{page_id}/child/page"), params={"limit": 100})
        resp.raise_for_status()
        return tuple((str(c["id"]), c["title"]) for c in resp.json().get("results", []))

    def can_read(self, page_id: str, account_id: str) -> bool:
        resp = self._http.post(
            self._url(f"/content/{page_id}/permission/check"),
            json={"subject": {"type": "user", "identifier": account_id}, "operation": "read"},
        )
        resp.raise_for_status()
        return bool(resp.json().get("hasPermission", False))


def _cql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _strip_highlight(excerpt: str) -> str:
    return re.sub(r"@@@(end)?hl@@@", "", excerpt).strip()


_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "table"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.parts)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
