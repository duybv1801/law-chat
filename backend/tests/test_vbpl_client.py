import asyncio
from datetime import date
from pathlib import Path

import httpx
import pytest
from tenacity import wait_none

from app.crawler.vbpl_client import (
    MAX_ATTEMPTS,
    ListFilters,
    RateLimiter,
    RobotsDisallowed,
    VbplClient,
    VbplHTTPError,
    VbplNotFound,
    _RetryWait,
)
from app.crawler.vbpl_parser import VbplError, VbplParseError

FIXTURES = Path(__file__).parent / "fixtures" / "vbpl"
ROBOTS = "User-Agent: *\nAllow: /\nDisallow: /api/\nDisallow: /Pages/\n"
DETAIL_32515 = "https://vbpl.vn/van-ban/chi-tiet/luat-hoa-giai-o-co-so-so-35-2013-qh13--32515"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeSleep:
    def __init__(self, clock: list[float]) -> None:
        self.clock = clock
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        await asyncio.sleep(0)  # yield like a real sleep, so other callers run meanwhile
        self.calls.append(seconds)
        self.clock[0] += seconds


class Site:
    """Fake vbpl.vn: path -> response (or a list of responses served in order)."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = {"/robots.txt": httpx.Response(200, text=ROBOTS), **routes}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get(request.url.path, httpx.Response(404))
        if isinstance(route, list):
            route = route.pop(0) if len(route) > 1 else route[0]
        if isinstance(route, Exception):
            raise route
        assert isinstance(route, httpx.Response)
        return route

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


def make_client(site: Site, **kwargs: object) -> VbplClient:
    clock = [0.0]
    kwargs.setdefault(
        "rate_limiter", RateLimiter(1.0, clock=lambda: clock[0], sleep=FakeSleep(clock))
    )
    return VbplClient(
        transport=httpx.MockTransport(site),
        user_agent="LawChatbot/test",
        retry_wait=wait_none(),
        **kwargs,  # type: ignore[arg-type]
    )


def detail_route(path: str = "/van-ban/chi-tiet/van-ban--32515") -> dict[str, object]:
    return {path: httpx.Response(200, content=fixture("detail_32515_luat_chl.html"))}


# --- Rate limiter -----------------------------------------------------------------------


async def test_rate_limiter_spaces_requests() -> None:
    clock = [100.0]
    sleep = FakeSleep(clock)
    limiter = RateLimiter(0.5, clock=lambda: clock[0], sleep=sleep)
    await limiter.wait()  # first request: no wait
    await limiter.wait()
    clock[0] += 0.5  # caller spent 0.5 s
    await limiter.wait()
    clock[0] += 10  # idle: no burst credit accumulates beyond one request
    await limiter.wait()
    await limiter.wait()
    assert sleep.calls == [2.0, 1.5, 2.0]


async def test_rate_limiter_concurrent_callers_queue() -> None:
    clock = [0.0]
    sleep = FakeSleep(clock)
    limiter = RateLimiter(1.0, clock=lambda: clock[0], sleep=sleep)
    await asyncio.gather(*(limiter.wait() for _ in range(3)))
    assert sorted(sleep.calls) == [1.0, 2.0]  # each caller reserved its own slot


def test_rate_limiter_rejects_zero() -> None:
    with pytest.raises(ValueError):
        RateLimiter(0)


# --- HTTP behaviour ---------------------------------------------------------------------


async def test_user_agent_and_robots_fetched_once() -> None:
    site = Site(detail_route())
    async with make_client(site) as client:
        await client.get_document("32515")
        await client.get_status("32515")
    assert site.paths() == ["/robots.txt", *["/van-ban/chi-tiet/van-ban--32515"] * 2]
    assert {r.headers["user-agent"] for r in site.requests} == {"LawChatbot/test"}
    assert client.requests == 3


async def test_robots_disallowed_path_never_requested() -> None:
    site = Site({})
    async with make_client(site) as client:
        with pytest.raises(RobotsDisallowed):
            await client.fetch("https://vbpl.vn/api/doc/1")
    assert site.paths() == ["/robots.txt"]


async def test_robots_forbidden_disallows_everything() -> None:
    site = Site({"/robots.txt": httpx.Response(403), **detail_route()})
    async with make_client(site) as client:
        with pytest.raises(RobotsDisallowed):
            await client.get_document("32515")


async def test_missing_robots_allows_everything() -> None:
    site = Site({"/robots.txt": httpx.Response(404), **detail_route()})
    async with make_client(site) as client:
        assert (await client.get_document("32515")).source_id == "32515"


async def test_retries_5xx_then_succeeds() -> None:
    page = fixture("detail_32515_luat_chl.html")
    site = Site(
        {
            "/van-ban/chi-tiet/van-ban--32515": [
                httpx.Response(503),
                httpx.Response(429, headers={"Retry-After": "1"}),
                httpx.Response(200, content=page),
            ]
        }
    )
    async with make_client(site) as client:
        doc = await client.get_document("32515")
    assert doc.so_hieu == "35/2013/QH13"
    assert site.paths().count("/van-ban/chi-tiet/van-ban--32515") == 3


async def test_retries_timeouts() -> None:
    path = "/van-ban/chi-tiet/van-ban--32515"
    timeout = httpx.ReadTimeout("slow")
    site = Site(
        {path: [timeout, httpx.Response(200, content=fixture("detail_32515_luat_chl.html"))]}
    )
    async with make_client(site) as client:
        await client.get_document("32515")
    assert site.paths().count(path) == 2


async def test_gives_up_after_max_attempts() -> None:
    site = Site({"/van-ban/chi-tiet/van-ban--32515": [httpx.Response(500)]})
    async with make_client(site) as client:
        with pytest.raises(VbplHTTPError) as err:
            await client.get_document("32515")
    assert err.value.status_code == 500
    assert site.paths().count("/van-ban/chi-tiet/van-ban--32515") == MAX_ATTEMPTS


@pytest.mark.parametrize("status", [400, 403])
async def test_other_4xx_not_retried(status: int) -> None:
    site = Site({"/van-ban/chi-tiet/van-ban--32515": httpx.Response(status)})
    async with make_client(site) as client:
        with pytest.raises(VbplHTTPError) as err:
            await client.get_document("32515")
    assert err.value.status_code == status
    assert site.paths().count("/van-ban/chi-tiet/van-ban--32515") == 1


async def test_404_is_not_found_without_retry() -> None:
    path = "/van-ban/chi-tiet/nghi-dinh-103-1997-nd-cp--vbpqta_1889"
    site = Site({path: httpx.Response(404, content=fixture("detail_vbpqta_1889_404.html"))})
    async with make_client(site) as client:
        with pytest.raises(VbplNotFound):
            await client.get_document("vbpqta_1889", f"https://vbpl.vn{path}")
    assert site.paths().count(path) == 1


async def test_200_without_document_is_parse_error() -> None:
    # e.g. an anti-bot page served with 200: reported, not retried.
    site = Site({"/van-ban/chi-tiet/van-ban--32515": httpx.Response(200, text="<html></html>")})
    async with make_client(site) as client:
        with pytest.raises(VbplParseError) as err:
            await client.get_document("32515")
    assert err.value.code == "no_initial_doc"
    assert site.paths().count("/van-ban/chi-tiet/van-ban--32515") == 1


async def test_redirect_off_site_rejected() -> None:
    redirect = httpx.Response(302, headers={"Location": "https://evil.example/x"})
    site = Site({"/van-ban/chi-tiet/van-ban--32515": redirect, "/x": httpx.Response(200)})
    async with make_client(site) as client:
        with pytest.raises(VbplHTTPError):
            await client.get_document("32515")


def test_retry_wait_honours_retry_after() -> None:
    from tenacity import RetryCallState, wait_fixed

    from app.crawler.vbpl_client import _Retryable

    state = RetryCallState(None, None, (), {})
    state.set_exception((_Retryable, _Retryable(429, "u", retry_after=30.0), None))
    assert _RetryWait(wait_fixed(2))(state) == 30.0
    state.set_exception((_Retryable, _Retryable(429, "u", retry_after=9999.0), None))
    assert _RetryWait(wait_fixed(2))(state) == 300.0
    state.set_exception((_Retryable, _Retryable(503, "u"), None))
    assert _RetryWait(wait_fixed(2))(state) == 2


async def test_cache_serves_second_fetch(tmp_path: Path) -> None:
    site = Site(detail_route())
    async with make_client(site, cache_dir=tmp_path) as client:
        first = await client.get_document("32515")
        again = await client.get_document("32515")
    assert first == again
    assert site.paths() == ["/robots.txt", "/van-ban/chi-tiet/van-ban--32515"]
    assert len(list(tmp_path.glob("*.body"))) == 1  # noqa: ASYNC240 (robots.txt not cached)

    site2 = Site({})  # new client, same cache: no request at all
    async with make_client(site2, cache_dir=tmp_path) as client:
        assert (await client.get_document("32515")).source_id == "32515"
    assert site2.requests == []


async def test_cache_hit_returns_same_text_as_miss(tmp_path: Path) -> None:
    body = "Đăng kí café".encode("windows-1258")
    headers = {"content-type": "text/html; charset=windows-1258"}
    site = Site({"/x": httpx.Response(200, content=body, headers=headers)})
    async with make_client(site, cache_dir=tmp_path) as client:
        miss = await client.fetch("https://vbpl.vn/x")
        hit = await client.fetch("https://vbpl.vn/x")
    assert miss == hit == "Đăng kí café"
    assert site.paths() == ["/robots.txt", "/x"]


# --- Documents and listing --------------------------------------------------------------


async def test_get_document_with_sitemap_url() -> None:
    site = Site(detail_route("/van-ban/chi-tiet/luat-hoa-giai-o-co-so-so-35-2013-qh13--32515"))
    async with make_client(site) as client:
        doc = await client.get_document("32515", DETAIL_32515)
        status = await client.get_status("32515", DETAIL_32515)
    assert doc.source_url == DETAIL_32515
    assert status == doc.status


async def test_get_document_id_mismatch() -> None:
    site = Site(detail_route("/van-ban/chi-tiet/van-ban--999"))
    async with make_client(site) as client:
        with pytest.raises(VbplParseError) as err:
            await client.get_document("999")
        assert err.value.code == "id_mismatch"
        with pytest.raises(VbplParseError):
            await client.get_status("999")


def sitemap(*entries: tuple[str, str]) -> bytes:
    urls = "".join(
        f"<url><loc>https://vbpl.vn/van-ban/chi-tiet/{slug}</loc><lastmod>{day}</lastmod></url>"
        for slug, day in entries
    )
    return f'<?xml version="1.0"?><urlset>{urls}</urlset>'.encode()


INDEX = b"""<sitemapindex><!-- Trang tinh --><sitemap><loc>https://vbpl.vn/sitemap/0.xml</loc></sitemap>
<!-- Trung \xc6\xb0\xc6\xa1ng --><sitemap><loc>https://vbpl.vn/sitemap/1.xml</loc></sitemap>
<sitemap><loc>https://vbpl.vn/sitemap/2.xml</loc></sitemap>
<!-- \xc4\x90\xe1\xbb\x8ba ph\xc6\xb0\xc6\xa1ng --><sitemap><loc>https://vbpl.vn/sitemap/3.xml</loc></sitemap>
</sitemapindex>"""


def listing_site() -> Site:
    return Site(
        {
            "/sitemap.xml": httpx.Response(200, content=INDEX),
            "/sitemap/1.xml": httpx.Response(
                200,
                content=sitemap(
                    ("luat-a--1", "2026-11-03"),
                    ("thong-tu-b--2", "2026-11-02"),
                    ("nghi-dinh-c--3", "2026-11-01"),
                ),
            ),
            "/sitemap/2.xml": httpx.Response(
                200, content=sitemap(("luat-d--4", "2026-10-31"), ("luat-e--5", "2026-10-20"))
            ),
            "/sitemap/3.xml": httpx.Response(
                200, content=sitemap(("quyet-dinh-f--6", "2026-11-03"))
            ),
        }
    )


async def test_list_documents_pages_and_filters() -> None:
    site = listing_site()
    async with make_client(site) as client:
        first = await client.list_documents(page=1, filters=ListFilters())
        luat = await client.list_documents(
            page=1, filters=ListFilters(doc_types=frozenset({"Luật"}))
        )
        second = await client.list_documents(page=2, filters=ListFilters())
        local = await client.list_documents(page=1, filters=ListFilters(scope="dia_phuong"))
        with pytest.raises(ValueError):
            await client.list_documents(page=3, filters=ListFilters())
    assert [r.source_id for r in first.items] == ["1", "2", "3"]
    assert (first.page, first.total_pages, first.next_page) == (1, 2, 2)
    assert [r.source_id for r in luat.items] == ["1"]
    assert second.next_page is None
    assert [r.source_id for r in local.items] == ["6"]
    assert site.paths().count("/sitemap.xml") == 1  # index read once per client


async def test_list_recently_updated_stops_at_since() -> None:
    site = listing_site()
    async with make_client(site) as client:
        refs = [r async for r in client.list_recently_updated(date(2026, 11, 1))]
        assert [r.source_id for r in refs] == ["1", "2", "3"]  # 2.xml starts before `since`

        refs = [r async for r in client.list_recently_updated(date(2026, 10, 25))]
    assert [r.source_id for r in refs] == ["1", "2", "3", "4"]
    assert refs[-1].lastmod == date(2026, 10, 31)
    assert "/sitemap/3.xml" not in site.paths()


async def test_redirect_hops_are_rate_limited_and_checked_against_robots() -> None:
    to_detail = httpx.Response(301, headers={"Location": "/van-ban/chi-tiet/van-ban--32515"})
    to_api = httpx.Response(302, headers={"Location": "/api/secret"})
    site = Site({"/old/32515": to_detail, "/old/api": to_api, **detail_route()})
    clock = [0.0]
    sleep = FakeSleep(clock)
    limiter = RateLimiter(1.0, clock=lambda: clock[0], sleep=sleep)
    async with make_client(site, rate_limiter=limiter) as client:
        doc = await client.get_document("32515", "https://vbpl.vn/old/32515")
        assert doc.source_id == "32515"
        with pytest.raises(RobotsDisallowed):
            await client.fetch("https://vbpl.vn/old/api")
    assert "/api/secret" not in site.paths()
    # robots.txt, /old/32515, detail, /old/api: every hop waits for its own slot.
    assert sleep.calls == [1.0, 1.0, 1.0]


async def test_off_site_url_never_requested() -> None:
    site = Site({})
    async with make_client(site) as client:
        with pytest.raises(VbplError):
            await client.fetch("https://evil.example/van-ban/chi-tiet/x--1")
    assert site.requests == []
