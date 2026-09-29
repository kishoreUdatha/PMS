"""Guest SMS and WhatsApp: numbers, payloads, and never pretending to send.

No network and no database. The MSG91 call itself is replaced, so these check
what would be sent and what the caller is told, which is where the mistakes
that cost money live: a message to a malformed number, a WhatsApp placeholder
filled in the wrong order, or a "sent" when nothing left.
"""
from __future__ import annotations

import pytest

from chirala_common import messenger
from chirala_common.guest_messages import Outcome, render, summary
from chirala_common.messenger import (
    MessagingConfig, MessagingError, MessagingNotConfigured, normalise_mobile,
    send_sms, send_whatsapp, sms_payload, whatsapp_payload,
)


# ------------------------------------------------------------- numbers --

@pytest.mark.parametrize("typed,expected", [
    ("9848022338", "919848022338"),
    ("98480 22338", "919848022338"),
    ("+91 98480-22338", "919848022338"),
    ("09848022338", "919848022338"),
    ("919848022338", "919848022338"),
    ("0091 9848022338", "919848022338"),
    ("6000000001", "916000000001"),
    # A foreign guest keeps their own country code.
    ("+44 7911 123456", "447911123456"),
    ("+1 (415) 555-0100", "14155550100"),
])
def test_numbers_come_out_as_msg91_wants_them(typed, expected):
    assert normalise_mobile(typed) == expected


@pytest.mark.parametrize("typed", [
    None, "", "   ", "12345", "5848022338",  # Indian mobiles start 6-9
    "040 2345 6789",  # a Hyderabad landline
    "abcdefghij", "+12",
])
def test_anything_that_is_not_a_mobile_is_refused(typed):
    assert normalise_mobile(typed) is None


# ------------------------------------------------------------ payloads --

def test_sms_sends_variables_by_name():
    p = sms_payload(to="919848022338", template_id="flow123",
                    variables={"guest_name": "Asha", "amount": "Rs 500.00"})
    assert p["template_id"] == "flow123"
    assert p["recipients"] == [{"mobiles": "919848022338",
                                "guest_name": "Asha", "amount": "Rs 500.00"}]


def test_whatsapp_sends_values_in_order():
    p = whatsapp_payload(from_number="918000000000", to="919848022338",
                         template_name="booking_confirmed", language="en",
                         values=["Asha", "Sea View", "CB-1042"])
    t = p["payload"]["template"]
    assert p["integrated_number"] == "918000000000"
    assert t["name"] == "booking_confirmed"
    assert t["to_and_components"][0]["to"] == ["919848022338"]
    comps = t["to_and_components"][0]["components"]
    assert [comps[f"body_{i}"]["value"] for i in (1, 2, 3)] == \
        ["Asha", "Sea View", "CB-1042"]


# --------------------------------------------------- sending, or not ----

def test_test_mode_sends_nothing(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("test mode must not call MSG91")
    monkeypatch.setattr(messenger, "_post", boom)
    cfg = MessagingConfig(test_mode=True)
    assert send_sms(cfg, to="919848022338", template_id="x", variables={}) is None
    assert send_whatsapp(cfg, to="919848022338", template_name="x", values=[]) is None


def test_unconfigured_refuses_loudly():
    with pytest.raises(MessagingNotConfigured):
        send_sms(MessagingConfig(), to="919848022338", template_id="x", variables={})
    # WhatsApp needs the sending number as well as the key.
    with pytest.raises(MessagingNotConfigured):
        send_whatsapp(MessagingConfig(auth_key="k"), to="919848022338",
                      template_name="x", values=[])


def test_a_real_send_posts_with_the_auth_key(monkeypatch):
    seen = {}

    def fake_post(config, url, payload):
        seen.update(url=url, key=config.auth_key, payload=payload)
        return {"type": "success", "message": "req-42"}

    monkeypatch.setattr(messenger, "_post", fake_post)
    ref = send_sms(MessagingConfig(auth_key="secret"), to="919848022338",
                   template_id="flow1", variables={"guest_name": "Asha"})
    assert ref == "req-42"
    assert seen["url"] == messenger.SMS_URL and seen["key"] == "secret"


def test_msg91_refusal_inside_a_200_is_an_error(monkeypatch):
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"type":"error","message":"Invalid template"}'

    monkeypatch.setattr(messenger.urllib.request, "urlopen",
                        lambda req, timeout: Resp())
    with pytest.raises(MessagingError, match="Invalid template"):
        send_sms(MessagingConfig(auth_key="k"), to="919848022338",
                 template_id="bad", variables={})


# ------------------------------------------------------------- wording --

def test_render_fills_known_names_and_blanks_the_rest():
    assert render("Hi {{guest_name}}, room {{ room }} {{x}}",
                  {"guest_name": "Asha", "room": "101"}) == "Hi Asha, room 101 "


def test_summary_reads_as_one_line():
    line = summary([Outcome("sms", "sent"),
                    Outcome("whatsapp", "skipped", "no template")])
    assert line == "SMS sent; WhatsApp not sent (no template)"
