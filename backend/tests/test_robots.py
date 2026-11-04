import pytest

from app.crawler.robots import RobotsRules

VBPL = """User-Agent: *
Allow: /
Disallow: /api/
Disallow: /Pages/

Sitemap: https://vbpl.vn/sitemap.xml
"""


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/van-ban/chi-tiet/luat--32515", True),
        ("/sitemap/1.xml", True),
        ("/", True),
        ("/api/doc/1", False),  # urllib.robotparser says True here (first match wins)
        ("/api/", False),
        ("/Pages/portal.aspx", False),
        ("/pages/portal.aspx", True),  # paths are case-sensitive
        ("/apix", True),
    ],
)
def test_vbpl_robots(path: str, allowed: bool) -> None:
    rules = RobotsRules.parse(VBPL, "LawChatbot/0.1 (+contact)")
    assert rules.can_fetch(f"https://vbpl.vn{path}") is allowed


def test_longest_match_and_tie_goes_to_allow() -> None:
    rules = RobotsRules.parse(
        "User-agent: *\nDisallow: /a\nAllow: /a/b\nDisallow: /c\nAllow: /c\n", "Bot"
    )
    assert not rules.can_fetch("https://x/a/c")
    assert rules.can_fetch("https://x/a/b/c")
    assert rules.can_fetch("https://x/c")


def test_wildcards_and_query() -> None:
    rules = RobotsRules.parse("User-agent: *\nDisallow: /*.pdf$\nDisallow: /*?print=\n", "Bot")
    assert not rules.can_fetch("https://x/files/a.pdf")
    assert rules.can_fetch("https://x/files/a.pdf.html")
    assert not rules.can_fetch("https://x/doc?print=1")
    assert rules.can_fetch("https://x/doc?view=1")


def test_specific_group_overrides_star() -> None:
    text = """# comment
User-agent: *
Disallow: /

User-agent: OtherBot
User-agent: lawchatbot
Disallow: /private  # inline comment
"""
    rules = RobotsRules.parse(text, "LawChatbot/0.1")
    assert rules.can_fetch("https://x/public")
    assert not rules.can_fetch("https://x/private/1")
    assert not RobotsRules.parse(text, "SomeoneElse/1").can_fetch("https://x/public")


def test_empty_disallow_and_no_rules() -> None:
    assert RobotsRules.parse("User-agent: *\nDisallow:\n", "Bot").can_fetch("https://x/a")
    assert RobotsRules.parse("", "Bot").can_fetch("https://x/a")
    assert not RobotsRules(disallow_all=True).can_fetch("https://x/a")
