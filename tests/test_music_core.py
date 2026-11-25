from __future__ import annotations

import pytest

from octapihub.application.music.queue import following, play_order, preceding
from octapihub.application.music.sources import SourceKind, parse_source
from octapihub.core.config import RepeatMode
from octapihub.core.errors import MusicSourceError

VIDEO = "dQw4w9WgXcQ"
LIST = "PLabcdefghijklmnop"


# ---------- sources ----------


@pytest.mark.parametrize(
    ("text", "kind", "uri"),
    [
        ("/home/pi/Music/Jazz", SourceKind.LOCAL, "local:/home/pi/Music/Jazz"),
        ("Jazz/Album", SourceKind.LOCAL, "local:Jazz/Album"),
        ("  Morgens  ", SourceKind.LOCAL, "local:Morgens"),
        ('"/home/pi/Mein Ordner"', SourceKind.LOCAL, "local:/home/pi/Mein Ordner"),
        ("file:///home/pi/Music/a%20b.mp3", SourceKind.LOCAL, "local:/home/pi/Music/a b.mp3"),
        ("file:///C:/Musik/lied.mp3", SourceKind.LOCAL, "local:C:/Musik/lied.mp3"),
        ("C:\\Musik\\lied.mp3", SourceKind.LOCAL, "local:C:\\Musik\\lied.mp3"),
        (
            "https://stream.example.org/radio.mp3",
            SourceKind.URL,
            "url:https://stream.example.org/radio.mp3",
        ),
        (
            "http://radio.example.org:8000/live",
            SourceKind.URL,
            "url:http://radio.example.org:8000/live",
        ),
        (f"https://www.youtube.com/watch?v={VIDEO}", SourceKind.YOUTUBE, f"youtube:{VIDEO}"),
        (
            f"https://music.youtube.com/watch?v={VIDEO}&list={LIST}",
            SourceKind.YOUTUBE,
            f"youtube:{VIDEO}?list={LIST}",
        ),
        (f"https://youtu.be/{VIDEO}", SourceKind.YOUTUBE, f"youtube:{VIDEO}"),
        (
            f"https://www.youtube.com/playlist?list={LIST}",
            SourceKind.YOUTUBE,
            f"youtube:?list={LIST}",
        ),
        (
            "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC",
            SourceKind.SPOTIFY,
            "spotify:track:4uLU6hMCjMI75M1A2tKUQC",
        ),
        (
            "https://open.spotify.com/intl-de/album/4uLU6hMCjMI75M1A2tKUQC?si=x",
            SourceKind.SPOTIFY,
            "spotify:album:4uLU6hMCjMI75M1A2tKUQC",
        ),
        (
            "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M",
            SourceKind.SPOTIFY,
            "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M",
        ),
    ],
)
def test_recognised_sources_are_normalised(text: str, kind: SourceKind, uri: str) -> None:
    source = parse_source(text)
    assert source.kind is kind
    assert source.uri == uri
    assert source.description


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "a\nb",
        "x" * 2001,
        "\\\\server\\share\\song.mp3",
        "//server/share",
        "../etc/passwd",
        "Jazz/../../etc",
        "ftp://example.org/x.mp3",
        "javascript://example.org",
        "https://user:pass@example.org/x.mp3",
        "https://exa mple.org/x",
        "https://",
        "https://www.youtube.com/watch?v=short",
        "https://www.youtube.com/",
        "https://open.spotify.com/user/someone",
        "https://open.spotify.com/track/!!",
        "spotify:track:bad",
        "spotify:podcast:4uLU6hMCjMI75M1A2tKUQC",
    ],
)
def test_unrecognised_or_unsafe_sources_are_rejected_with_a_german_explanation(text: str) -> None:
    with pytest.raises(MusicSourceError) as error:
        parse_source(text)
    assert error.value.retryable is False
    assert error.value.error_code == "CH-MUS-003"
    assert "Erkannt werden" in error.value.user_message


def test_non_text_input_is_rejected() -> None:
    with pytest.raises(MusicSourceError):
        parse_source(None)  # type: ignore[arg-type]


def test_a_malformed_youtube_list_is_ignored_but_the_video_still_plays() -> None:
    source = parse_source(f"https://www.youtube.com/watch?v={VIDEO}&list=x")
    assert source.playlist is None
    assert source.youtube_url == f"https://www.youtube.com/watch?v={VIDEO}"


def test_youtube_urls_are_canonical() -> None:
    assert (
        parse_source(f"https://youtu.be/{VIDEO}?list={LIST}").youtube_url
        == f"https://www.youtube.com/watch?v={VIDEO}&list={LIST}"
    )
    assert (
        parse_source(f"https://www.youtube.com/playlist?list={LIST}").youtube_url
        == f"https://www.youtube.com/playlist?list={LIST}"
    )


def test_descriptions_distinguish_the_kinds() -> None:
    texts = {
        parse_source(x).description
        for x in (
            "/music",
            "https://a.example/s.mp3",
            f"https://youtu.be/{VIDEO}",
            f"https://youtu.be/{VIDEO}?list={LIST}",
            f"https://www.youtube.com/playlist?list={LIST}",
            "spotify:track:4uLU6hMCjMI75M1A2tKUQC",
        )
    }
    assert len(texts) == 6


# ---------- queue rules ----------


def test_play_order_is_a_permutation_and_reproducible() -> None:
    assert play_order(0, shuffle=True) == []
    assert play_order(-3, shuffle=True) == []
    assert play_order(5, shuffle=False) == [0, 1, 2, 3, 4]
    mixed = play_order(20, shuffle=True, seed=3)
    assert sorted(mixed) == list(range(20))
    assert mixed != list(range(20))
    assert mixed == play_order(20, shuffle=True, seed=3)
    assert mixed != play_order(20, shuffle=True, seed=4)
    assert play_order(1, shuffle=True) == [0]


def test_shuffle_keeps_the_picked_track_first() -> None:
    for seed in range(10):
        order = play_order(8, shuffle=True, seed=seed, first=5)
        assert order[0] == 5
        assert sorted(order) == list(range(8))
    assert play_order(4, shuffle=False, first=2) == [0, 1, 2, 3]  # no shuffle: no reordering


@pytest.mark.parametrize(
    ("position", "count", "repeat", "by_user", "expected"),
    [
        (0, 3, RepeatMode.OFF, False, 1),
        (2, 3, RepeatMode.OFF, False, None),
        (2, 3, RepeatMode.ALL, False, 0),
        (1, 3, RepeatMode.ONE, False, 1),  # the track repeats by itself
        (1, 3, RepeatMode.ONE, True, 2),  # but the next button moves on
        (2, 3, RepeatMode.ONE, True, 0),
        (2, 3, RepeatMode.OFF, True, None),
        (0, 0, RepeatMode.ALL, False, None),
        (0, 1, RepeatMode.ALL, False, 0),
        (0, 1, RepeatMode.OFF, False, None),
    ],
)
def test_following(
    position: int, count: int, repeat: RepeatMode, by_user: bool, expected: int | None
) -> None:
    assert following(position, count, repeat, by_user=by_user) == expected


@pytest.mark.parametrize(
    ("position", "count", "repeat", "expected"),
    [
        (2, 3, RepeatMode.OFF, 1),
        (0, 3, RepeatMode.OFF, 0),  # the first track starts over
        (0, 3, RepeatMode.ALL, 2),
        (0, 3, RepeatMode.ONE, 0),
        (0, 0, RepeatMode.ALL, 0),
    ],
)
def test_preceding(position: int, count: int, repeat: RepeatMode, expected: int) -> None:
    assert preceding(position, count, repeat) == expected
