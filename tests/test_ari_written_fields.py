"""A field somebody wrote is sent, even when the channel already has it.

Channex certification test 7 sets "min stay 1" on nights whose minimum stay
was already 1. The sync diffed it away and the check failed. The outbox now
carries the fields a rate-plan calendar save changed, and ``_dirty_scope``
turns them into keys the sync must send. No database: the two lookups it
makes are stubbed.
"""

from __future__ import annotations

from datetime import date


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Db:
    """Answers the room-mapping query, then the plan-mapping query."""

    def __init__(self, rooms, plans):
        self._answers = [_Rows(rooms), _Rows(plans)]

    def execute(self, *_a, **_k):
        return self._answers.pop(0)


def _scope(rows):
    from booking_core.channel_sync import _dirty_scope

    db = _Db(rooms=[("room-1", "EXT-ROOM")],
             plans=[("plan-1", "EXT-PLAN", "room-1"),
                    ("plan-2", "EXT-OTHER", "room-1")])
    return _dirty_scope(db, {"id": "link"}, rows)


def test_written_fields_become_keys_for_every_night_of_the_row():
    lo, hi, wanted, written = _scope([{
        "scope": "rates", "room_type_id": None, "rate_plan_id": "plan-1",
        "date_from": date(2026, 11, 1), "date_to": date(2026, 11, 3),
        "fields": ["min_stay_arrival", "min_stay_through"]}])
    assert (lo, hi) == (date(2026, 11, 1), date(2026, 11, 3))
    days = [date(2026, 11, d) for d in (1, 2, 3)]
    assert written == {(f, "EXT-PLAN", d)
                       for f in ("min_stay_arrival", "min_stay_through")
                       for d in days}
    # Only the plan that was written, not its sibling on the same room.
    assert not any(k[1] == "EXT-OTHER" for k in written)


def test_rows_without_fields_force_nothing():
    _, _, wanted, written = _scope([{
        "scope": "rates", "room_type_id": None, "rate_plan_id": "plan-1",
        "date_from": date(2026, 11, 1), "date_to": date(2026, 11, 1),
        "fields": []}])
    assert written == set()
    assert wanted(("rate", "EXT-PLAN", date(2026, 11, 1)))
