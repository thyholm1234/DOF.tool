"""Den fælles markdown-renderer (artikler, nyheder, kommentarer): rendering, portable referencer og sanitering."""

import pytest

from portal.services import content_render as cr

MEDIA = {7: cr.Media(7, "billede", "/media/art/ab/x.jpg", "/media/art/ab/m.jpg", "/media/art/ab/t.jpg", 1000, 800, alt="Gemt alt"),
         8: cr.Media(8, "pdf", "/media/art/cd/r.pdf", filename="r.pdf", size=3_200_000)}
HOSTS = {"xeno-canto.org", "player.vimeo.com"}


def render(md, profile="artikel", **kw):
    return cr.render(md, profile, media=kw.pop("media", MEDIA), species=kw.pop("species", {5: "Dværgterne"}), embed_hosts=kw.pop("embed_hosts", HOSTS))


def test_markdown_basics_tables_and_containers():
    r = render("## Overskrift\n\n**fed** _kursiv_ ~~væk~~\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n::: boks Fakta\nIndhold\n:::\n\n::: galleri\n![](media:7)\n:::")
    assert "<h2>Overskrift</h2>" in r.html and "<strong>fed</strong>" in r.html and "<s>væk</s>" in r.html and "<table>" in r.html
    assert '<div class="art-boks"><div class="art-boks-titel">Fakta</div>' in r.html and '<div class="art-galleri">' in r.html
    assert "Overskrift" in r.text and "Fakta" in r.text and "<" not in r.text


def test_portable_references_are_resolved():
    r = render('Se [artiklen](netfugl:155), [en anden](artikel:9), [rapport](media:8) og [[art:5]].\n\n![billede](media:7 "Billedtekst")')
    assert 'href="#/artikler/n/155"' in r.html and 'href="#/artikler/9"' in r.html and 'href="/media/art/cd/r.pdf" class="art-pdf"' in r.html.replace(' rel="noopener noreferrer"', "")
    assert '<a class="art-chip" href="#/arter/5"' in r.html and ">Dværgterne</a>" in r.html
    assert 'src="/media/art/ab/m.jpg"' in r.html and 'width="1000" height="800"' in r.html and "400w" in r.html and 'class="art-cap">Billedtekst<' in r.html
    assert r.media_ids == [7, 8] and r.species == [5]


def test_missing_references_warn_instead_of_breaking():
    r = render("![x](media:99) [y](media:98) [[art:42]]")
    assert "media:99" not in r.html and len(r.warnings) == 2 and "[[art:42]]" in r.html


@pytest.mark.parametrize("evil", [
    "<script>alert(1)</script>", "<img src=x onerror=alert(1)>", "<a href=\"javascript:alert(1)\">x</a>", "[x](javascript:alert(1))",
    "<div style=\"position:fixed\" onclick=\"x()\">x</div>", "<svg onload=alert(1)></svg>", "<object data=x></object>", "<embed src=x>",
    "<form action=x><input></form>", "<style>body{display:none}</style>", "<iframe src=\"javascript:alert(1)\"></iframe>",
    "<iframe src=\"https://evil.example/x\"></iframe>", "<iframe src=\"http://xeno-canto.org/1\"></iframe>", "<iframe src=\"https://xeno-canto.org.evil.example/1\"></iframe>",
    "<img src=\"https://tracker.example/p.gif\">", "<a href=\"data:text/html,<script>1</script>\">x</a>", "<meta http-equiv=refresh content=0>",
])
def test_dangerous_html_is_removed(evil):
    h = render(f"før\n\n{evil}\n\nefter").html.lower()
    for bad in ("<script", "onerror", "onclick", "onload", 'href="javascript:', 'src="javascript:', "<style", "<form", "<object", "<embed", "<svg", "<meta", "tracker.example", "evil.example", "style=", "data:text"):
        assert bad not in h, (bad, h)
    assert "xeno-canto.org/1" not in h or "https://xeno-canto.org/1" in h


def test_allowed_iframe_is_sandboxed_and_class_whitelist_applies():
    h = render('<iframe src="https://xeno-canto.org/123/embed" width="300"></iframe>\n\n<div class="art-boks hack">x</div>').html
    assert 'src="https://xeno-canto.org/123/embed"' in h and 'sandbox="' in h and 'loading="lazy"' in h and 'referrerpolicy="no-referrer"' in h
    assert 'class="art-boks"' in h and "hack" not in h
    assert "<iframe" not in render('<iframe src="https://vimeo.com/1"></iframe>', embed_hosts=set()).html


def test_external_images_become_links_and_external_links_are_safe():
    r = render("![logo](https://tracker.example/x.png) [ext](https://example.com)")
    assert "<img" not in r.html and 'href="https://tracker.example/x.png"' in r.html and r.warnings
    assert 'target="_blank"' in r.html and 'rel="noopener noreferrer"' in r.html


def test_comment_profile_is_minimal():
    r = cr.render("Hej **du** <b>x</b> <script>1</script> ![i](https://a.example/i.png) [rel](/internt) [ok](https://example.com)\n\n# Overskrift\n\n| a |\n|---|\n| b |", "kommentar")
    assert "<strong>du</strong>" in r.html and "<b>" not in r.html and "<script>" not in r.html and "<img" not in r.html and "<h1" not in r.html and "<table" not in r.html
    assert 'href="/internt"' not in r.html and 'rel="noopener noreferrer nofollow ugc"' in r.html and "&lt;b&gt;x&lt;/b&gt;" in r.html
    with pytest.raises(ValueError):
        cr.render("x", "ukendt")


def test_text_slug_teaser_helpers():
    assert cr.slugify("Aztekerterne på Langli – 2001!") == "aztekerterne-paa-langli-2001"
    assert cr.slugify("Ærø og Østerlars") == "aeroe-og-oesterlars"
    assert cr.teaser("ord " * 200, 50).endswith("…") and len(cr.teaser("ord " * 200, 50)) <= 55
    assert cr.teaser("kort") == "kort"
    assert cr.to_text("<p>Afsnit 1</p><p>Afsnit&nbsp;2 &amp; mere</p><ul><li>a</li><li>b</li></ul>") == "Afsnit 1\nAfsnit 2 & mere\na\nb"
    assert cr.size_label(3_200_000) == "3,2 MB" and cr.size_label(1500) == "2 kB"
