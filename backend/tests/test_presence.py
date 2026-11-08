"""Presence from retained online messages and last-will offline messages."""

import pytest

from app import presence


@pytest.fixture(autouse=True)
def clean():
    presence.reset()
    yield
    presence.reset()


@pytest.mark.parametrize(
    "raw,expected",
    [
        (b"online", (True, None)),
        (b"offline\n", (False, None)),
        (b'{"online": true, "conn": "a1"}', (True, "a1")),
        (b'{"online": false}', (False, None)),
        (b'{"online": "yes"}', None),
        (b"[]", None),
        (b"maybe", None),
    ],
)
def test_parse(raw, expected):
    assert presence.parse(raw) == expected


def test_unknown_robots_are_not_offline():
    assert not presence.is_offline(1)


def test_will_marks_the_robot_offline():
    # The first sighting counts as a change: after a restart the retained
    # messages are how the dashboard learns who is offline.
    assert presence.apply(1, True, "a") is True
    assert presence.apply(1, False, "a") is True
    assert presence.is_offline(1)


def test_stale_will_from_an_older_connection_is_ignored():
    presence.apply(1, True, "old")
    presence.apply(1, True, "new")  # reconnected before the broker noticed
    assert presence.apply(1, False, "old") is False
    assert not presence.is_offline(1)
    assert presence.apply(1, False, "new") is True


def test_offline_without_connection_ids_is_accepted():
    presence.apply(1, True, None)
    assert presence.apply(1, False, None) is True


def test_repeated_messages_are_not_changes():
    presence.apply(1, False, "a")
    assert presence.apply(1, False, "a") is False
    assert presence.apply(1, True, "b") is True
    assert presence.apply(1, True, "b") is False


def test_traffic_repairs_a_wrong_offline():
    presence.apply(1, False, "stale")
    assert presence.saw_traffic(1) is True
    assert not presence.is_offline(1)
    assert presence.saw_traffic(1) is False


def test_traffic_from_an_unknown_robot_is_not_news():
    assert presence.saw_traffic(9) is False
    assert presence.get(9).online
