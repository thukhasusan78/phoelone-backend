from __future__ import annotations

import re
from typing import Any

from app.config import Settings
from app.tools.http import SafeHttp


class NewsTool:
    name = "search_news"
    declaration = {
        "name": "search_news",
        "description": "Search recent news headlines by topic. Summarize in Burmese speech; do not read URLs.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Topic or search query"},
                "topic": {"type": "string", "description": "Alias for query"},
                "count": {"type": "integer", "description": "Number of headlines (1-5)"},
            },
        },
    }

    def __init__(self, http: SafeHttp, settings: Settings) -> None:
        self.http = http
        self.settings = settings

    async def __call__(
        self,
        query: str | None = None,
        topic: str | None = None,
        count: int = 3,
        **_: Any,
    ) -> dict[str, Any]:
        text = (query or topic or "").strip()
        if not text:
            return {"error": "query is required"}
        count = max(1, min(int(count or 3), 5))
        if self.settings.tavily_key:
            data = await self.http.post_json(
                "https://api.tavily.com/search",
                {
                    "api_key": self.settings.tavily_key,
                    "query": text,
                    "topic": "news",
                    "max_results": count,
                    "search_depth": "basic",
                },
            )
            items = [
                {"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content")}
                for r in (data.get("results") or [])[:count]
            ]
            return {"query": text, "headlines": items}

        rss = await self.http.get_text(
            "https://news.google.com/rss/search",
            params={"q": text, "hl": "my", "gl": "MM", "ceid": "MM:my"},
        )
        titles = re.findall(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", rss)
        headlines = [{"title": t} for t in titles[1 : count + 1]]
        return {"query": text, "headlines": headlines}
