"""Polite HTTP client for vbpl.vn (rule 6: <= 1 request/second, honest User-Agent, retries
with backoff, robots.txt).

Listing reads sitemaps and documents come from detail pages (`docs/vbpl-findings.md`):
`/api/` is disallowed by robots.txt, so it is never called. All markup knowledge is in
`vbpl_parser.py`; this module only fetches.
"""

import asyncio
import hashlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import TracebackType
from typing import Self
from urllib.parse import urljoin, urlsplit

import httpx
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tenacity.wait import wait_base

from app.core.config import settings
from app.crawler.robots import RobotsRules
from app.crawler.vbpl_parser import (
    DocRef,
    SitemapFile,
    VbplDocument,
    VbplError,
    VbplStatus,
    parse_detail,
    parse_sitemap,
    parse_sitemap_index,
    parse_status,
)
from app.ingest.schemas import Scope

logger = logging.getLogger(__name__)

BASE_URL = "https://vbpl.vn"
TIMEOUT_S = 30.0
MAX_ATTEMPTS = 4  # first try + 3 retries
MAX_RETRY_AFTER_S = 300.0
MAX_REDIRECTS = 5


class VbplNotFound(VbplError):
    """404 / 410. Expected for some sitemap URLs (`--vbpqta_*`): skip, do not retry."""


class VbplHTTPError(VbplError):
    def __init__(self, status_code: int, url: str, retry_after: float | None = None) -> None:
        super().__init__(f"HTTP {status_code} for {url}")
        self.status_code = status_code
        self.retry_after = retry_after


class _Retryable(VbplHTTPError):
    """429 / 5xx: retried with backoff."""


class RobotsDisallowed(VbplError):
    pass


class RateLimiter:
    """Token bucket with capacity 1: consecutive requests start >= `1 / rps` seconds apart.

    Each caller reserves the next free slot before sleeping, so concurrent coroutines queue
    up without a lock (asyncio is single-threaded) and the limiter is not tied to one loop.
    """

    def __init__(
        self,
        rps: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rps <= 0:
            raise ValueError("rps must be > 0")
        self.interval = 1.0 / rps
        self._clock = clock
        self._sleep = sleep
        self._next = float("-inf")

    async def wait(self) -> None:
        now = self._clock()
        start = max(now, self._next)
        self._next = start + self.interval
        if start > now:
            await self._sleep(start - now)


# One limiter per process: every client (CLI, scheduler jobs, admin runs) shares the budget.
_limiter: RateLimiter | None = None


def global_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(settings.crawler_rate_limit_rps)
    return _limiter


@dataclass(frozen=True)
class ListFilters:
    scope: Scope = "trung_uong"
    # Estimated from the URL slug (`guess_doc_type`); None = every type. Status and the
    # real doc type are only known after fetching the detail page.
    doc_types: frozenset[str] | None = None

    def accepts(self, ref: DocRef) -> bool:
        return self.doc_types is None or ref.doc_type_guess in self.doc_types


@dataclass(frozen=True)
class ListPage:
    """One sitemap file of the scope. `page` is 1-based; checkpoint = (page, position)."""

    page: int
    total_pages: int
    items: list[DocRef]

    @property
    def next_page(self) -> int | None:
        return self.page + 1 if self.page < self.total_pages else None


class _RetryWait(wait_base):
    """Exponential backoff, but never shorter than the server's `Retry-After`."""

    def __init__(self, base: wait_base) -> None:
        self.base = base

    def __call__(self, retry_state: RetryCallState) -> float:
        wait = self.base(retry_state)
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        retry_after = getattr(exc, "retry_after", None)
        if retry_after:
            wait = max(wait, min(retry_after, MAX_RETRY_AFTER_S))
        return wait


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers["retry-after"])
    except (KeyError, ValueError):
        return None  # missing, or an HTTP date (rare): plain backoff


class VbplClient:
    """Use as `async with VbplClient() as client: ...`."""

    def __init__(
        self,
        *,
        base_url: str = BASE_URL,
        user_agent: str | None = None,
        rate_limiter: RateLimiter | None = None,
        cache_dir: Path | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_wait: wait_base | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._limiter = rate_limiter or global_rate_limiter()
        if cache_dir is None and settings.crawler_cache:
            cache_dir = settings.crawler_cache_dir
        self._cache_dir = cache_dir
        self._retry_wait = _RetryWait(retry_wait or wait_exponential(multiplier=2, max=60))
        self.user_agent = user_agent or settings.crawler_user_agent
        self._http = httpx.AsyncClient(
            headers={"User-Agent": self.user_agent},
            timeout=TIMEOUT_S,
            # Redirects are followed in `_get`: each hop goes through the rate limiter, the
            # robots.txt check and the on-site check.
            follow_redirects=False,
            transport=transport,
        )
        self._robots: RobotsRules | None = None
        self._sitemaps: list[SitemapFile] | None = None
        self.requests = 0  # network requests made (cache hits excluded)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- HTTP -----------------------------------------------------------------------------

    def detail_url(self, source_id: str) -> str:
        # The site resolves documents by the id after "--"; the slug is ignored.
        return f"{self.base_url}/van-ban/chi-tiet/van-ban--{source_id}"

    def _cache_path(self, url: str) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()}.body"

    async def _request(self, url: str) -> httpx.Response:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(MAX_ATTEMPTS),
            wait=self._retry_wait,
            # TransportError covers timeouts and connection errors.
            retry=retry_if_exception_type((_Retryable, httpx.TransportError)),
            reraise=True,
        ):
            with attempt:
                await self._limiter.wait()
                self.requests += 1
                response = await self._http.get(url)
                status = response.status_code
                if status == 429 or status >= 500:
                    logger.warning("vbpl.vn HTTP %s for %s", status, url)
                    raise _Retryable(status, url, _retry_after(response))
                return response
        raise AssertionError("unreachable")  # reraise=True raises the last error

    async def _get(self, url: str, *, check_robots: bool = True) -> httpx.Response:
        """GET following up to `MAX_REDIRECTS` redirects, never leaving vbpl.vn."""
        site = urlsplit(self.base_url).netloc
        if urlsplit(url).netloc != site:
            raise VbplError(f"not a vbpl.vn URL: {url}")
        target = url
        for _ in range(MAX_REDIRECTS + 1):
            if check_robots and not (await self._ensure_robots()).can_fetch(target):
                raise RobotsDisallowed(target)
            response = await self._request(target)
            location = response.headers.get("location")
            if not response.is_redirect or not location:
                return response
            target = urljoin(target, location)
            if urlsplit(target).netloc != site:
                raise VbplHTTPError(response.status_code, f"{url} (redirected off-site)")
        raise VbplHTTPError(response.status_code, f"{url} (too many redirects)")

    async def _ensure_robots(self) -> RobotsRules:
        if self._robots is None:
            response = await self._get(f"{self.base_url}/robots.txt", check_robots=False)
            if response.status_code in (401, 403):
                self._robots = RobotsRules(disallow_all=True)
            elif response.status_code >= 400:
                self._robots = RobotsRules()  # no robots.txt: everything is allowed
            else:
                self._robots = RobotsRules.parse(response.text, self.user_agent)
        return self._robots

    async def fetch(self, url: str) -> str:
        """Body of a 200 response as text. Raises `VbplNotFound`, `VbplHTTPError`,
        `RobotsDisallowed`, or httpx errors once retries are exhausted."""
        cache = self._cache_path(url)
        if cache is not None and cache.is_file():
            return cache.read_bytes().decode("utf-8", "replace")

        response = await self._get(url)
        if response.status_code in (404, 410):
            raise VbplNotFound(url)
        if response.status_code != 200:
            raise VbplHTTPError(response.status_code, url)

        text = response.text
        if cache is not None:
            # Cache the decoded text as UTF-8, so a hit returns exactly what a miss did.
            cache.parent.mkdir(parents=True, exist_ok=True)
            part = cache.with_suffix(".part")
            part.write_bytes(text.encode("utf-8"))
            part.replace(cache)
        return text

    # --- Listing --------------------------------------------------------------------------

    async def sitemaps(self, scope: Scope) -> list[str]:
        """Sitemap file URLs of a scope, in index order (newest `lastmod` first)."""
        if self._sitemaps is None:
            self._sitemaps = parse_sitemap_index(await self.fetch(f"{self.base_url}/sitemap.xml"))
        return [f.url for f in self._sitemaps if f.scope == scope]

    async def _sitemap(self, url: str) -> list[DocRef]:
        return await asyncio.to_thread(parse_sitemap, await self.fetch(url))

    async def list_documents(self, *, page: int, filters: ListFilters) -> ListPage:
        files = await self.sitemaps(filters.scope)
        if not 1 <= page <= len(files):
            raise ValueError(f"page {page} out of range 1..{len(files)}")
        refs = await self._sitemap(files[page - 1])
        return ListPage(
            page=page, total_pages=len(files), items=[r for r in refs if filters.accepts(r)]
        )

    async def list_recently_updated(
        self, since: date, *, scope: Scope = "trung_uong"
    ) -> AsyncIterator[DocRef]:
        """Documents with `lastmod >= since`, newest first. Stops at the first older entry:
        the files of a scope form one list sorted by `lastmod` descending."""
        for url in await self.sitemaps(scope):
            for ref in await self._sitemap(url):
                if ref.lastmod is None:
                    continue
                if ref.lastmod < since:
                    return
                yield ref

    # --- Documents ------------------------------------------------------------------------

    async def get_document(self, source_id: str, url: str | None = None) -> VbplDocument:
        """Metadata + full text. `url` (from the sitemap or DB) is optional."""
        target = url or self.detail_url(source_id)
        return await asyncio.to_thread(parse_detail, await self.fetch(target), target, source_id)

    async def get_status(self, source_id: str, url: str | None = None) -> VbplStatus:
        """Validity fields only. There is no lighter endpoint: the detail page is fetched."""
        page = await self.fetch(url or self.detail_url(source_id))
        return await asyncio.to_thread(parse_status, page, source_id)
