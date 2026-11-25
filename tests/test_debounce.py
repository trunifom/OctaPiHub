from octapihub.domain.cube import OrientationDebouncer, OrientationKind, reconnect_delay_seconds


def test_first_value_is_baseline_without_action() -> None:
    debouncer = OrientationDebouncer(debounce_ms=300)

    events = debouncer.feed(3, 100)

    assert [(event.kind, event.value) for event in events] == [(OrientationKind.BASELINE, 3)]


def test_only_stable_value_is_committed() -> None:
    debouncer = OrientationDebouncer(debounce_ms=300)
    debouncer.feed(1, 100)

    assert debouncer.feed(4, 200) == []
    assert debouncer.feed(6, 250) == []
    assert debouncer.feed(6, 300) == []

    events = debouncer.tick(550)
    assert [(event.kind, event.value) for event in events] == [(OrientationKind.FACE_CHANGE, 6)]


def test_return_to_committed_value_cancels_candidate() -> None:
    debouncer = OrientationDebouncer(debounce_ms=300)
    debouncer.feed(1, 100)
    debouncer.feed(4, 200)

    assert debouncer.feed(1, 250) == []
    assert debouncer.pending_value is None
    assert debouncer.tick(600) == []


def test_neutral_is_distinct_from_baseline() -> None:
    debouncer = OrientationDebouncer(debounce_ms=100)
    debouncer.feed(2, 100)
    debouncer.feed(0, 200)

    events = debouncer.tick(300)
    assert [(event.kind, event.value) for event in events] == [(OrientationKind.NEUTRAL, 0)]


def test_reconnect_backoff_is_bounded() -> None:
    assert reconnect_delay_seconds(0) == 1.0
    assert reconnect_delay_seconds(1) == 1.0
    assert reconnect_delay_seconds(3) == 4.0
    assert reconnect_delay_seconds(100) == 60.0
