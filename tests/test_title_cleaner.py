"""Unit tests for YouTube title cleaner."""

from helpers.title_cleaner import clean_music_title, parse_title_and_artist


def test_clean_music_title_variations():
    """Test cleaning various common YouTube music video title suffixes and tags."""
    cases = [
        (
            "Kodak Black - Tunnel Vision [Official Music Video]",
            "Kodak Black - Tunnel Vision",
        ),
        (
            "Adele - Hello (Official Music Video)",
            "Adele - Hello",
        ),
        (
            "The Weeknd - Blinding Lights (Official Audio)",
            "The Weeknd - Blinding Lights",
        ),
        (
            "Dua Lipa - Levitating [Official Lyric Video]",
            "Dua Lipa - Levitating",
        ),
        (
            "Coldplay - Yellow [HD]",
            "Coldplay - Yellow",
        ),
        (
            "Drake - God's Plan (Audio)",
            "Drake - God's Plan",
        ),
        (
            "Eminem - Venom (Music Video)",
            "Eminem - Venom",
        ),
        (
            "Travis Scott - HIGHEST IN THE ROOM [Official Visualizer]",
            "Travis Scott - HIGHEST IN THE ROOM",
        ),
        (
            "Billie Eilish - bad guy | Official Video",
            "Billie Eilish - bad guy",
        ),
    ]

    for raw, expected in cases:
        assert clean_music_title(raw) == expected, f"Failed on: {raw}"


def test_parse_title_and_artist():
    """Test parsing clean title, artist, and track name."""
    title, artist, song = parse_title_and_artist(
        "Kodak Black - Tunnel Vision [Official Music Video]",
        fallback_artist="Kodak Black",
    )
    assert title == "Kodak Black - Tunnel Vision"
    assert artist == "Kodak Black"
    assert song == "Tunnel Vision"

    title2, artist2, song2 = parse_title_and_artist(
        "Tunnel Vision [Official Video]",
        fallback_artist="Kodak Black",
    )
    assert title2 == "Tunnel Vision"
    assert artist2 == "Kodak Black"
    assert song2 == "Tunnel Vision"
