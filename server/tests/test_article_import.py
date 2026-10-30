"""Rene dele af arkivimporten: zip-sikkerhed, markdown-omskrivning, emneordsklassificering og kommentartræ (ingen database)."""

import io
import zipfile

import pytest

from portal.services import article_import as ai


def make(tmp_path, members: dict[str, bytes | str], root="netfugl-artikler"):
    p = tmp_path / "a.zip"
    with zipfile.ZipFile(p, "w") as z:
        for name, data in members.items():
            z.writestr(name.replace("{r}", root), data)
    return p


@pytest.mark.parametrize("name,ok", [("netfugl-artikler/0012/meta.json", True), ("0012/meta.json", True), ("../x", False), ("a/../../x", False),
                                     ("netfugl-artikler/0005/../../escape.txt", False), ("/etc/passwd", False), ("a\\b", False), ("C:/x", False), ("", False)])
def test_safe_name(name, ok):
    assert (ai.safe_name(name) is not None) == ok


def test_open_archive_finds_root_ignores_index_and_turrapporter(tmp_path):
    p = make(tmp_path, {"{r}/MANIFEST.md": "x", "{r}/_index.json": "[]", "{r}/Turrapporter/0001/meta.json": "{}", "{r}/0012/meta.json": "{}", "{r}/0012/artikel.md": "x",
                        "{r}/0012/original.html": "<html>", "{r}/0012/billeder/1.jpg": b"x", "{r}/0012/billeder/1.svg": b"x", "{r}/0012/filer/a.pdf": b"%PDF",
                        "{r}/0012/filer/a.exe": b"MZ", "{r}/0012/filer/a.ocr.md": "t", "{r}/0013/meta.json": "{}", "{r}/readme.txt": "x"})
    a = ai.open_archive(p)
    assert a.root == "netfugl-artikler" and sorted(a.articles) == [12, 13] and "_index.json" in a.other
    assert sorted(a.articles[12]) == ["artikel.md", "billeder/1.jpg", "filer/a.ocr.md", "filer/a.pdf", "meta.json"]
    assert a.ignored == 4 and not a.rejected and "readme.txt" in a.other


def test_open_archive_without_manifest_and_with_flat_root(tmp_path):
    assert sorted(ai.open_archive(make(tmp_path, {"0012/meta.json": "{}"})).articles) == [12]
    with pytest.raises(ai.ImportAbort):
        ai.open_archive(make(tmp_path, {"hej.txt": "x"}))


def test_open_archive_rejects_unsafe_members_and_symlinks(tmp_path):
    p = tmp_path / "a.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("netfugl-artikler/MANIFEST.md", "x")
        z.writestr("netfugl-artikler/0001/meta.json", "{}")
        z.writestr("../escape.txt", "x")
        z.writestr("netfugl-artikler/0001/../../escape2.txt", "x")
        info = zipfile.ZipInfo("netfugl-artikler/0002/meta.json")
        info.external_attr = (0o120777 << 16)  # symlink
        z.writestr(info, "/etc/passwd")
    a = ai.open_archive(p)
    assert len(a.rejected) == 3 and sorted(a.articles) == [1]


def test_open_archive_rejects_zip_bomb(tmp_path):
    p = tmp_path / "a.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("netfugl-artikler/MANIFEST.md", "x")
        z.writestr("netfugl-artikler/0001/meta.json", "{}")
        z.writestr("netfugl-artikler/0001/filer/bomb.txt", b"0" * (50 * 1024 * 1024))  # komprimerer ~1000:1
    a = ai.open_archive(p)
    assert "filer/bomb.txt" not in a.articles[1] and any("komprimeringsforhold" in r for r in a.rejected)


def test_not_a_zip_and_too_many_members(tmp_path, monkeypatch):
    bad = tmp_path / "b.zip"
    bad.write_bytes(b"nope")
    with pytest.raises(ai.ImportAbort):
        ai.open_archive(bad)
    monkeypatch.setattr(ai, "MAX_MEMBERS", 2)
    with pytest.raises(ai.ImportAbort):
        ai.open_archive(make(tmp_path, {"{r}/MANIFEST.md": "x", "{r}/0001/meta.json": "{}", "{r}/0002/meta.json": "{}"}))


def test_fingerprint_changes_with_content(tmp_path):
    def fp(text):
        a = ai.open_archive(make(tmp_path, {"{r}/MANIFEST.md": "x", "{r}/0001/meta.json": "{}", "{r}/0001/artikel.md": text}))
        return ai.fingerprint(a.articles[1])

    assert fp("a") == fp("a") and fp("a") != fp("b")


def test_read_member_is_bounded(tmp_path, monkeypatch):
    a = ai.open_archive(make(tmp_path, {"{r}/MANIFEST.md": "x", "{r}/0001/meta.json": "x" * 100}))
    monkeypatch.setattr(ai, "MAX_MEMBER_BYTES", 10)
    with pytest.raises(ai.ImportAbort):
        ai.read_member(a.zf, a.articles[1]["meta.json"])


def test_rewrite_markdown_title_cover_media_and_links():
    md = ("# Min titel\n\n**Min  titel**\n\n![](billeder/001-a%20b.jpg)\n\nTekst [fil](filer/r.pdf) og [en anden](../0155/artikel.md#x) og "
          "[web](https://www.netfugl.dk/artikler/300/slug) samt [ekstern](https://example.com) ![ekstra](billeder/002.jpg \"Tekst\") ![mangler](billeder/9.jpg)\n")
    rel = {"billeder/001-a b.jpg": 1, "billeder/002.jpg": 2, "filer/r.pdf": 3}
    out, warn = ai.rewrite_markdown(md, "Min titel", rel, cover_rel="billeder/001-a b.jpg")
    assert out.startswith("Tekst [fil](media:3)")
    assert "[en anden](netfugl:155)" in out and "[web](netfugl:300)" in out and "[ekstern](https://example.com)" in out
    assert '![ekstra](media:2 "Tekst")' in out and "![mangler](billeder/9.jpg)" in out and any("9.jpg" in w for w in warn)
    kept, _ = ai.rewrite_markdown(md, "Min titel", rel, cover_rel=None)
    assert kept.startswith("![](media:1)")  # uden forsidebillede bliver billedet stående


def test_rewrite_markdown_keeps_different_second_line_and_external_images():
    out, warn = ai.rewrite_markdown("# Titel\n\nAnden overskrift\n\n![x](https://netfugl.dk/pictures/a.jpg)", "Titel", {}, None)
    assert out.startswith("Anden overskrift") and "https://netfugl.dk/pictures/a.jpg" in out and not warn
    out, _ = ai.rewrite_markdown("", "T", {}, None)
    assert out == ""
    out, _ = ai.rewrite_markdown("[![alt](https://netfugl.dk/p/a.jpg)](https://www.netfugl.dk/pictures.php?id=1)Tekst", "T", {"https://netfugl.dk/p/a.jpg": 5}, None)
    assert out.startswith("[![alt](media:5)](https://www.netfugl.dk/pictures.php?id=1)")


def test_gone_images_keep_their_caption():
    md = "[![Gråsejler, Chrø 1987](https://netfugl.dk/p/a.jpg)](https://www.netfugl.dk/pictures.php?id=1)Tekst ![](https://netfugl.dk/p/b.jpg) ![ok](https://netfugl.dk/p/c.jpg)"
    gone = {"https://netfugl.dk/p/a.jpg", "https://netfugl.dk/p/b.jpg"}
    out, _ = ai.rewrite_markdown(md, "T", {"https://netfugl.dk/p/c.jpg": 9}, None, gone)
    assert out == "[*Gråsejler, Chrø 1987*](https://www.netfugl.dk/pictures.php?id=1)Tekst  ![ok](media:9)"


def test_external_images_only_from_netfugl():
    md = "![a](https://netfugl.dk/pictures/x.jpg) ![b](https://evil.example/y.jpg) ![c](billeder/z.jpg) ![d](http://www.netfugl.dk/q.png) ![a](https://netfugl.dk/pictures/x.jpg)"
    assert ai.external_images(md) == ["https://netfugl.dk/pictures/x.jpg", "http://www.netfugl.dk/q.png"]
    assert len(ai.external_images(" ".join(f"![i](https://netfugl.dk/{i}.jpg)" for i in range(100)))) == ai.MAX_EXTERNAL_PER_ARTICLE


def test_classify_tag():
    species = {"høgeørn": 12}
    assert ai.classify_tag("2020", species) == ("aar", None) and ai.classify_tag("Nyhed", species) == ("type", None)
    assert ai.classify_tag("DOF NYT", species) == ("publikation", None) and ai.classify_tag("Høgeørn", species) == ("art", 12)
    assert ai.classify_tag("Skagen", species) == ("emne", None) and ai.classify_tag("NFL (Ny For Landet/førstegangsfund)", species)[0] == "emne"


def test_flatten_comments_two_levels_and_dates():
    tree = [{"forfatter": "A", "svar": [{"forfatter": "B", "svar": [{"forfatter": "C", "svar": []}]}]}, {"forfatter": "D", "svar": []}]
    flat = ai.flatten_comments(tree)
    assert [(c["forfatter"], t["forfatter"] if t else None) for c, t in flat] == [("A", None), ("B", "A"), ("C", "A"), ("D", None)]
    assert ai.parse_dato("2000-06-20T01:10").utcoffset().total_seconds() == 7200  # dansk sommertid
    assert ai.parse_dato("2000-01-20T01:10").utcoffset().total_seconds() == 3600
    assert ai.parse_dato("skrald", None).tzinfo is not None
    assert ai.comment_key(1, "a", "d", "t", 0) != ai.comment_key(1, "a", "d", "t", 1) == ai.comment_key(1, "a", "d", "t", 1)


def test_article_date_falls_back_to_danish_text_date():
    d = ai.article_date({"dato": "2000-06-20T01:10", "dato_original": "x"})[0]
    assert (d.year, d.month, d.day, d.hour) == (2000, 6, 20, 1)
    d, warn = ai.article_date({"dato": None, "dato_original": "1. okt. 2008"})
    assert (d.year, d.month, d.day, d.hour) == (2008, 10, 1, 0) and warn is None
    d, _ = ai.article_date({"dato": None, "dato_original": "9. maj 2001 kl. 20:27"})
    assert (d.year, d.month, d.day, d.hour, d.minute) == (2001, 5, 9, 20, 27)
    assert ai.parse_danish_date("12. marts 2004").month == 3 and ai.parse_danish_date("3. juli 1999").month == 7
    d, warn = ai.article_date({"dato": None, "dato_original": "ugyldig", "uploadet_af": {"dato": "2010-02-03T04:05"}})
    assert d.year == 2010 and warn
    assert ai.article_date({})[1] and ai.parse_danish_date("31. feb. 2001") is None


def test_ocr_plain_removes_header():
    out = ai.ocr_plain("# Titel\n\n> Tekst udtrukket fra `x.pdf`\n\n## Side 1\n\nFørste linje\n\n\n\n## Side 2\n\nAnden")
    assert out == "Første linje\n\nAnden"


def test_unused_io():
    assert io.BytesIO(b"").read() == b""
