"""What the sign-in emails say.

Composed here rather than inline at the call sites so the wording is in one
place and can be read as prose by somebody who is not reading SQL.

**No password is ever in one of these.** The welcome mail carries the property
code and the address to sign in with, and a single-use link to choose a
password. A password sent by email survives in the mailbox, in the sender's
outbox, and in whatever logs sat between the two — for years, and long after
it has been changed. A link that expires does not.
"""
from __future__ import annotations

from datetime import date, timedelta

from .settings import settings


def _link(path: str, token: str) -> str:
    base = settings.app_base_url.rstrip("/")
    return f"{base}{path}?token={token}"


def _first_name(name: str) -> str:
    return name.split()[0] if name.strip() else "there"


def _support_text() -> str:
    """The support line, or nothing at all.

    An email that says "contact us at {{support_email}}" because nobody filled
    the setting in is worse than one that does not offer help.
    """
    bits = [b for b in (settings.support_email, settings.support_phone) if b]
    if not bits:
        return ""
    return f"\nStuck? Contact {' or '.join(bits)}.\n"


def _support_html() -> str:
    bits = [b for b in (settings.support_email, settings.support_phone) if b]
    if not bits:
        return ""
    return (f'<p style="color:#64748b;font-size:13px">Stuck? Contact '
            f'{" or ".join(bits)}.</p>')


def _text_table(rows: list[tuple[str, str]]) -> str:
    """An ASCII table that lines up whatever is put in it.

    Widths are measured rather than guessed: a property called "The Bay" and
    one called "Chirala Bay Beach Resort & Spa" cannot share a hard-coded
    column. Plain +-| characters rather than box-drawing, because a mail
    client using a proportional font for plain text will mangle either, and
    these at least stay legible when it does.
    """
    label_w = max(len(label) for label, _ in rows)
    value_w = max(len(value) for _, value in rows)
    rule = f"  +{'-' * (label_w + 2)}+{'-' * (value_w + 2)}+"
    out = [rule]
    for label, value in rows:
        out.append(f"  | {label.ljust(label_w)} | {value.ljust(value_w)} |")
        out.append(rule)
    return "\n".join(out)


def _html_table(rows: list[tuple[str, str]]) -> str:
    """The same table, bordered, for clients that render HTML."""
    cells = "".join(
        f'<tr>'
        f'<td style="border:1px solid #e2e8f0;padding:10px 14px;'
        f'background:#f8fafc;color:#475569;white-space:nowrap">{label}</td>'
        f'<td style="border:1px solid #e2e8f0;padding:10px 14px;'
        f'color:#0f172a"><strong>{value}</strong></td>'
        f'</tr>'
        for label, value in rows
    )
    return (f'<table style="border-collapse:collapse;margin:18px 0;'
            f'font-size:14px">{cells}</table>')


def welcome_email(
    *, name: str, property_name: str, property_code: str, email: str,
    token: str, hours: int,
) -> tuple[str, str, str]:
    """Subject, plain text and HTML for the first sign-in."""
    url = _link("/set-password", token)
    first = _first_name(name)
    platform = settings.platform_name
    # A date, not "in 72 hours". Nobody counts hours forward from an email
    # they opened on Monday morning having received it on Friday.
    expires = (date.today() + timedelta(hours=hours)).strftime("%d %B")
    subject = f"Welcome to {platform} — Your property setup is complete"

    text = f"""Hello {first},

{property_name} is now set up on {platform}, and an account is waiting for
you.

Your login details

{_text_table([
    ("Property name", property_name),
    ("Property code", property_code),
    ("Username", email),
    ("Password", "choose one using the link below"),
])}

Set your password:
{url}

The link works once and expires on {expires}. If it has gone by the time you
get to it, use "Forgot password" on the sign-in page and we will send another.

We never send passwords by email, and nobody here can read yours.

Not expecting this? Somebody has created an account in your name. Tell
whoever runs {property_name} before using the link.
{_support_text()}
Regards,
The {platform} team
"""

    html = f"""<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;
  color:#1e293b;max-width:560px;line-height:1.6">
  <p>Hello {first},</p>
  <p><strong>{property_name}</strong> is now set up on {platform}, and an
     account is waiting for you.</p>
  <p><strong>Your login details</strong></p>
  {_html_table([
      ("Property name", property_name),
      ("Property code", property_code),
      ("Username", email),
      ("Password", "choose one using the button below"),
  ])}
  <p><a href="{url}" style="display:inline-block;background:#0f766e;color:#fff;
     padding:12px 22px;border-radius:10px;text-decoration:none;
     font-weight:600">Set your password</a></p>
  <p style="color:#64748b;font-size:13px">The link works once and expires on
     <strong>{expires}</strong>. If it has gone by the time you get to it, use
     &ldquo;Forgot password&rdquo; on the sign-in page and we will send
     another.</p>
  <p style="color:#64748b;font-size:13px">We never send passwords by email,
     and nobody here can read yours.</p>
  <p style="color:#64748b;font-size:13px">Not expecting this? Somebody has
     created an account in your name &mdash; tell whoever runs
     {property_name} before using the link.</p>
  {_support_html()}
  <p style="color:#94a3b8;font-size:13px">Regards,<br>The {platform} team</p>
</div>"""
    return subject, text, html


def reset_email(
    *, name: str, property_name: str, property_code: str, email: str,
    token: str,
) -> tuple[str, str, str]:
    """Subject, plain text and HTML for a password reset."""
    url = _link("/set-password", token)
    first = _first_name(name)
    platform = settings.platform_name
    subject = f"Reset your {property_name} password"

    text = f"""Hello {first},

Somebody asked to reset the password for {email} at {property_name}
(property code {property_code}).

Choose a new password:
{url}

The link works once and expires in 2 hours.

If this was not you, nothing has changed and you can ignore this message —
but it is worth telling your manager that somebody tried.
{_support_text()}
Regards,
The {platform} team
"""

    html = f"""<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;
  color:#1e293b;max-width:560px;line-height:1.6">
  <p>Hello {first},</p>
  <p>Somebody asked to reset the password for <strong>{email}</strong> at
     <strong>{property_name}</strong> (property code {property_code}).</p>
  <p><a href="{url}" style="display:inline-block;background:#0f766e;color:#fff;
     padding:12px 22px;border-radius:10px;text-decoration:none;
     font-weight:600">Choose a new password</a></p>
  <p style="color:#64748b;font-size:13px">The link works once and expires in
     2 hours.</p>
  <p style="color:#64748b;font-size:13px">If this was not you, nothing has
     changed and you can ignore this message &mdash; but it is worth telling
     your manager that somebody tried.</p>
  {_support_html()}
  <p style="color:#94a3b8;font-size:13px">Regards,<br>The {platform} team</p>
</div>"""
    return subject, text, html


def verification_email(*, name: str, code: str, minutes: int) -> tuple[str, str, str]:
    """Subject, plain text and HTML for the sign-up code.

    Short on purpose. The only thing anybody wants from this email is the six
    digits, and every extra line is something between them and it -- which is
    also why the code is in the subject: most people can read it from the
    notification without opening anything.
    """
    first = _first_name(name)
    platform = settings.platform_name
    subject = f"{code} is your {platform} verification code"

    text = f"""Hello {first},

Your verification code is:

    {code}

It expires in {minutes} minutes. Type it into the sign-up page to confirm
this address is yours.

If you did not start creating an account, ignore this message -- nothing has
been created, and the code is useless without the rest of the form.
{_support_text()}
Regards,
The {platform} team
"""

    html = f"""<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;
  color:#1e293b;max-width:560px;line-height:1.6">
  <p>Hello {first},</p>
  <p>Your verification code is:</p>
  <p style="font-size:34px;font-weight:700;letter-spacing:10px;
     color:#0f766e;margin:18px 0">{code}</p>
  <p style="color:#64748b;font-size:13px">It expires in {minutes} minutes.
     Type it into the sign-up page to confirm this address is yours.</p>
  <p style="color:#64748b;font-size:13px">If you did not start creating an
     account, ignore this message &mdash; nothing has been created, and the
     code is useless without the rest of the form.</p>
  {_support_html()}
  <p style="color:#94a3b8;font-size:13px">Regards,<br>The {platform} team</p>
</div>"""
    return subject, text, html


def property_live_email(
    *, name: str, property_name: str, property_code: str, email: str,
) -> tuple[str, str, str]:
    """For somebody who already has a password: the details, no link.

    The owner sets a password at sign-up, so they need no set-password link --
    but the property code is generated by the server and shown nowhere they
    would think to look, and it is required at sign-in. Skipping them meant
    the one person who finished onboarding was the one person who could not
    get back in.

    So they get the same table and no link: a live "set your password" link
    sent to somebody who already has one is indistinguishable from a phishing
    attempt.
    """
    first = _first_name(name)
    platform = settings.platform_name
    sign_in = settings.app_base_url.rstrip("/") + "/login"
    subject = f"{property_name} is live — your sign-in details"

    text = f"""Hello {first},

Setup is complete and {property_name} is live on {platform}.

Your login details

{_text_table([
    ("Property name", property_name),
    ("Property code", property_code),
    ("Username", email),
    ("Password", "the one you chose at sign-up"),
])}

Sign in:
{sign_in}

Keep the property code somewhere you can find it — everyone at
{property_name} needs it to sign in, and it is not shown anywhere else.

Forgotten your password? Use "Forgot password" on the sign-in page.
{_support_text()}
Regards,
The {platform} team
"""

    html = f"""<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;
  color:#1e293b;max-width:560px;line-height:1.6">
  <p>Hello {first},</p>
  <p>Setup is complete and <strong>{property_name}</strong> is live on
     {platform}.</p>
  <p><strong>Your login details</strong></p>
  {_html_table([
      ("Property name", property_name),
      ("Property code", property_code),
      ("Username", email),
      ("Password", "the one you chose at sign-up"),
  ])}
  <p><a href="{sign_in}" style="display:inline-block;background:#0f766e;
     color:#fff;padding:12px 22px;border-radius:10px;text-decoration:none;
     font-weight:600">Sign in</a></p>
  <p style="color:#64748b;font-size:13px">Keep the property code somewhere you
     can find it &mdash; everyone at {property_name} needs it to sign in, and
     it is not shown anywhere else.</p>
  <p style="color:#64748b;font-size:13px">Forgotten your password? Use
     &ldquo;Forgot password&rdquo; on the sign-in page.</p>
  {_support_html()}
  <p style="color:#94a3b8;font-size:13px">Regards,<br>The {platform} team</p>
</div>"""
    return subject, text, html


#: The required steps, in order, exactly as the wizard presents them. Optional
#: ones (team, import, channel connections) are left out on purpose: a list of
#: nine things reads as a chore, and five of them are what actually gates
#: going live. Kept in step with ONBOARDING_STEPS in platform_routes.
_SETUP_STEPS = [
    ("Property details", "address, contact, check-in times"),
    ("Buildings and floors", "how the property is laid out"),
    ("Rooms and room types", "what you sell, and how many"),
    ("Rates and plans", "prices, and what each includes"),
    ("Invoice and tax setup", "GST details and invoice numbering"),
]


def owner_invitation_email(
    *, name: str, property_name: str, property_code: str, email: str,
    token: str, hours: int,
) -> tuple[str, str, str]:
    """The first email a new tenant's owner receives.

    Deliberately not welcome_email, which goes to staff when a property goes
    live. This one is its opposite: nothing is set up yet, and the person
    reading it is the one who has to do it. Sending them "your property setup
    is complete" -- which is what they used to get -- told them there was
    nothing to do, so nothing was done.

    It has one job: get them to set a password and start the wizard. So the
    link is the only action, the steps are named so the work is a known
    quantity rather than an open one, and the fact that progress is saved is
    stated, because "do I have to finish this now?" is the reason people put
    an email like this aside.
    """
    url = _link("/set-password", token)
    first = _first_name(name)
    platform = settings.platform_name
    expires = (date.today() + timedelta(hours=hours)).strftime("%d %B")
    subject = f"Set up {property_name} on {platform}"

    steps_text = "\n".join(
        f"  {i}. {label} — {detail}"
        for i, (label, detail) in enumerate(_SETUP_STEPS, start=1))
    steps_html = "".join(
        f'<li style="margin-bottom:6px"><strong>{label}</strong>'
        f'<span style="color:#64748b"> — {detail}</span></li>'
        for label, detail in _SETUP_STEPS)

    text = f"""Hello {first},

An account has been created for {property_name} on {platform}. Nothing is set
up yet -- that part is yours, and this email is where it starts.

Start here:
{url}

That link sets your password and takes you straight into setup. It works once
and expires on {expires}; if it has gone by the time you get to it, use
"Forgot password" on the sign-in page.

Keep these -- you will need the property code to sign in:

{_text_table([
    ("Property", property_name),
    ("Property code", property_code),
    ("Username", email),
])}

What setup involves

{steps_text}

You can stop after any step and come back -- your progress is saved, and the
property is not bookable until you choose to go live. Most properties finish
in under an hour.

We never send passwords by email, and nobody here can read yours.

Not expecting this? Somebody has created an account in your name. Tell
whoever runs {property_name} before using the link.
{_support_text()}
Regards,
The {platform} team
"""

    html = f"""<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;
  color:#1e293b;max-width:560px;line-height:1.6">
  <p>Hello {first},</p>
  <p>An account has been created for <strong>{property_name}</strong> on
     {platform}. Nothing is set up yet &mdash; that part is yours, and this
     email is where it starts.</p>
  <p><a href="{url}" style="display:inline-block;background:#006E78;color:#fff;
     padding:13px 24px;border-radius:10px;text-decoration:none;
     font-weight:600">Set your password and start setup</a></p>
  <p style="color:#64748b;font-size:13px">The link works once and expires on
     <strong>{expires}</strong>. If it has gone by the time you get to it, use
     &ldquo;Forgot password&rdquo; on the sign-in page.</p>
  <p><strong>Keep these</strong> &mdash; you will need the property code to
     sign in:</p>
  {_html_table([
      ("Property", property_name),
      ("Property code", property_code),
      ("Username", email),
  ])}
  <p><strong>What setup involves</strong></p>
  <ol style="padding-left:20px;margin:0 0 16px">{steps_html}</ol>
  <p style="color:#475569;font-size:14px">You can stop after any step and come
     back &mdash; your progress is saved, and the property is not bookable
     until you choose to go live. Most properties finish in under an hour.</p>
  <p style="color:#64748b;font-size:13px">We never send passwords by email,
     and nobody here can read yours.</p>
  <p style="color:#64748b;font-size:13px">Not expecting this? Somebody has
     created an account in your name &mdash; tell whoever runs
     {property_name} before using the link.</p>
  {_support_html()}
  <p style="color:#94a3b8;font-size:13px">Regards,<br>The {platform} team</p>
</div>"""
    return subject, text, html
