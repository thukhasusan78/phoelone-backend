from __future__ import annotations

from typing import Any
from urllib.parse import quote

from app.config import Settings
from app.tools.http import SafeHttp


class KnowledgeTool:
    name = "search_web"
    declaration = {
        "name": "search_web",
        "description": (
            "Search the web for facts and general knowledge. "
            "Return short facts; the model will speak Burmese."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    }

    def __init__(self, http: SafeHttp, settings: Settings) -> None:
        self.http = http
        self.settings = settings

    async def __call__(self, query: str, **_: Any) -> dict[str, Any]:
        text = (query or "").strip()
        if not text:
            return {"error": "query is required"}
        if self.settings.tavily_key:
            try:
                return await self._tavily(text)
            except Exception:
                pass
        return await self._wikipedia(text)

    async def _tavily(self, query: str) -> dict[str, Any]:
        data = await self.http.post_json(
            "https://api.tavily.com/search",
            {
                "api_key": self.settings.tavily_key,
                "query": query,
                "max_results": 5,
                "search_depth": "basic",
                "include_answer": True,
            },
        )
        return {
            "query": query,
            "source": "tavily",
            "answer": data.get("answer"),
            "results": [
                {"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content")}
                for r in (data.get("results") or [])[:5]
            ],
        }

    async def _wikipedia(self, query: str) -> dict[str, Any]:
        search = await self.http.get_json(
            "https://en.wikipedia.org/w/rest.php/v1/search/page",
            params={"q": query, "limit": 3},
        )
        pages = search.get("pages") or []
        if not pages:
            return {"query": query, "source": "wikipedia", "error": "no results"}
        title = pages[0].get("title") or query
        key = (pages[0].get("key") or title).replace(" ", "_")
        summary: dict[str, Any] = {}
        try:
            summary = await self.http.get_json(
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(key, safe='')}"
            )
        except Exception:
            summary = {}
        extract = (
            summary.get("extract")
            or pages[0].get("description")
            or pages[0].get("excerpt")
        )
        return {
            "query": query,
            "source": "wikipedia",
            "answer": extract,
            "results": [
                {
                    "title": p.get("title"),
                    "url": "https://en.wikipedia.org/wiki/"
                    + (p.get("key") or p.get("title") or "").replace(" ", "_"),
                    "snippet": p.get("description") or p.get("excerpt"),
                }
                for p in pages[:3]
            ],
        }
