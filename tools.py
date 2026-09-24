from __future__ import annotations
import httpx
import trafilatura
from tavily import TavilyClient

import config
from models import SearchResult, SearchResponse, PageContent


def web_search(query: str, max_results: int | None = None) -> SearchResponse:
    """Search the web via Tavily and return structured results."""
    max_results = max_results or config.SEARCH_MAX_RESULTS
    if not config.TAVILY_API_KEY:
        return SearchResponse(query=query, results=[], error="TAVILY_API_KEY not set")

    try:
        client = TavilyClient(api_key=config.TAVILY_API_KEY)
        response = client.search(
            query=query,
            max_results=max_results,
            include_raw_content=True,
            search_depth="advanced",
        )
    except Exception as e:
        return SearchResponse(query=query, results=[], error=str(e))

    results = []
    for r in response.get("results", []):
        raw = r.get("raw_content") or None
        if raw and len(raw) > 15_000:
            raw = raw[:15_000] + "\n... [truncated]"
        results.append(SearchResult(
            title=r.get("title", ""),
            url=r.get("url", ""),
            snippet=r.get("content", ""),
            raw_content=raw,
            score=r.get("score"),
        ))

    return SearchResponse(query=query, results=results)


def fetch_page(url: str, timeout: int | None = None) -> PageContent:
    """Fetch a URL and extract its main text content.

    Uses trafilatura's built-in fetcher as the primary method (handles
    User-Agent, retries, and encoding well across sites including Wikipedia).
    Falls back to httpx if trafilatura's fetcher fails.
    """
    custom_timeout = timeout is not None
    timeout = timeout or config.FETCH_TIMEOUT_SECONDS

    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    html: str | None = None

    # When a custom timeout is passed, use httpx so the timeout is respected.
    # Otherwise prefer trafilatura's fetcher (better User-Agent handling).
    if custom_timeout:
        try:
            resp = httpx.get(url, timeout=timeout, follow_redirects=True, headers=_HEADERS)
            resp.raise_for_status()
            html = resp.text
        except httpx.TimeoutException:
            return PageContent(url=url, title="", text="", error=f"Timeout after {timeout}s")
        except httpx.HTTPStatusError as e:
            return PageContent(url=url, title="", text="", error=f"HTTP {e.response.status_code}")
        except Exception as e:
            return PageContent(url=url, title="", text="", error=str(e))
    else:
        try:
            html = trafilatura.fetch_url(url)
        except Exception:
            pass
        if not html:
            try:
                resp = httpx.get(url, timeout=timeout, follow_redirects=True, headers=_HEADERS)
                resp.raise_for_status()
                html = resp.text
            except httpx.TimeoutException:
                return PageContent(url=url, title="", text="", error=f"Timeout after {timeout}s")
            except httpx.HTTPStatusError as e:
                return PageContent(url=url, title="", text="", error=f"HTTP {e.response.status_code}")
            except Exception as e:
                return PageContent(url=url, title="", text="", error=str(e))

    if not html:
        return PageContent(url=url, title="", text="", error="Could not download page")

    title = ""
    try:
        meta = trafilatura.extract_metadata(html)
        if meta and meta.title:
            title = meta.title
    except Exception:
        pass

    extracted = trafilatura.extract(
        html,
        include_links=False,
        include_tables=True,
        favor_recall=True,
    )

    if not extracted:
        return PageContent(url=url, title=title, text="", error="No content extracted from page")

    if len(extracted) > 20_000:
        extracted = extracted[:20_000] + "\n... [truncated]"

    return PageContent(url=url, title=title, text=extracted)


# Tool definitions for the OpenAI function-calling API.
# The agent loop will reference these when building its messages.
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for information. Use this to find current facts, "
                "news, data, or evidence about a topic. Returns titles, URLs, "
                "snippets, and optionally full page content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum results to return (default 5).",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": (
                "Fetch and extract the main text content of a specific URL. "
                "Use this when you need the full content of a page you found "
                "via web_search, or to verify a specific source."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch.",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_lookup",
            "description": (
                "Look up what is already known about a named entity from prior "
                "research. Returns stored facts and sources if the entity has "
                "been researched before. Use this before searching the web to "
                "avoid redundant work."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_name": {
                        "type": "string",
                        "description": "The name of the entity to look up (company, person, etc).",
                    },
                },
                "required": ["entity_name"],
            },
        },
    },
]


TOOL_DISPATCH = {
    "web_search": web_search,
    "fetch_page": fetch_page,
}
