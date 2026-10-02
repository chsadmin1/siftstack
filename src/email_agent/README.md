# Email cadence

A one-way outbound email drip for the same "D x D" board the two-way SMS agent
(`src/sms_agent/`) already works, built to support it rather than duplicate it.

## What this is, and is not

**Is:** a three-touch email sequence, rendered from a proven copy pool,
tracked per address so nobody gets the same touch twice, sent through
DataSift's own connected mailbox.

**Is not:** two-way. There is no reply classification, no AI-drafted replies,
no escalation engine. That is `sms_agent`'s job. The one exception is
`optout-scan`, a narrow regex safety net (see below) — not a conversation
engine.

## Why it shares so much with the SMS agent

Three things must never disagree between the two channels, so they are
imported rather than re-declared:

- **The board** (`config.CAMPAIGN_PRESET`, default `sms_agent.config.CAMPAIGN_PRESET`,
  "D x D"). Same cohort, same suppression rules already encoded in the preset.
- **Sender identity** (`sms_config.SENDERS_FILE`, `config/sms_senders.json`).
  An email is signed by the same person a text from that record would be
  signed by, resolved the same way, held back the same way when unmapped.
- **The opt-out tag** ("Do Not Market", `sms_config.TAG_OPT_OUT`). A person who
  opts out of one channel is excluded from the other automatically, because
  both check the same tag on the record.

## Setup

```bash
python src/email_agent/cli.py doctor
```

**1. Connect a mailbox.** DataSift Settings > Integrations > Email. Sending
fails with no connected mailbox — `doctor` says so.

**2. Set the required env vars** (`.env`):

```
EMAIL_AGENT_PHYSICAL_ADDRESS="Your Company, 123 Main St, Knoxville, TN 37902"
EMAIL_AGENT_TEST_RECIPIENT=you@yourdomain.com
```

`EMAIL_AGENT_PHYSICAL_ADDRESS` is not optional. CAN-SPAM requires a valid
physical postal address in every commercial email; sending is blocked without
one, the same way the SMS agent refuses to send an unsigned text.

**3. Verify the send-email payload shape with one real test send:**

```bash
python src/email_agent/cli.py doctor --test-send
```

`POST .../email-integration/{id}/send-email/` is not documented past its
path — `10-integrations.md` names the endpoint but not its body.
`mailer.send_email()` guesses the common shape (`{to, subject, body}`). If the
test send 400s, the error text names the real field the API wants; fix the
payload in `mailer.py` and try again. **Do not release a batch before this
passes.** Same discipline as `datasift_api_upload.py`: verify one, read it
back, before trusting the rest.

## Running it

```bash
python src/email_agent/cli.py plan                  # preview today's batch, no writes
python src/email_agent/cli.py plan -v                # + every subject line
python src/email_agent/cli.py send                   # preview (same as plan, no --commit = no send)
python src/email_agent/cli.py send --commit           # send for real
```

`EMAIL_AGENT_DRY_RUN=1` (the default) blocks every send regardless of
`--commit`, same posture as `SMS_AGENT_DRY_RUN`. Unset it (`EMAIL_AGENT_DRY_RUN=0`)
when ready to go live.

There is no persistent worker or receiver here, unlike the SMS agent — email
sending is a single API call, not a rate-limited multi-number scheduling
problem. Run `send --commit` once a day (cron, Task Scheduler, or by hand);
`store.sent_today()` enforces `EMAIL_AGENT_DAILY_CAP` (default 150) across
however many times it runs in a day.

## The three touches

Intro, follow-up, gentle close, spaced `EMAIL_AGENT_TOUCH_GAP_DAYS` apart
(default 4 — longer than the SMS agent's 1-day gap, because a same-sender
inbox cadence reads as spam much faster than a same-sender phone cadence, and
email has no per-line daily cap forcing pacing the way texting does).

Progression is per email address, from that address's own send history, same
rule as the SMS side: someone who already had touch 1 gets touch 2 next, not
whatever touch their *property* happens to imply.

Voice matches the texts: warm, first name only (entities and initials-only
owners get owner-of-the-address wording via the same `clean_first`/`is_entity`
functions the SMS pool uses), never names the list (foreclosure, probate, tax,
lien, etc.), no dollar figure, no company name, signed with the assigned
rep's first name. `validate.py` enforces this on the rendered body before the
compliance footer is appended.

## The footer

Every email gets, after validation:

```
---
{EMAIL_AGENT_PHYSICAL_ADDRESS}
If you'd rather not hear from {sender} again, just reply and say so and you won't.
```

No link. A hyperlink in a cold outreach email reads as a mail-merge and hurts
deliverability as much as it hurts the human read — same reasoning the SMS
agent has for never including one. "Reply and say so" satisfies CAN-SPAM's
opt-out requirement without one.

## `optout-scan`: the one exception to "one-way"

```bash
python src/email_agent/cli.py optout-scan              # dry: report what it would suppress
python src/email_agent/cli.py optout-scan --commit       # suppress locally + tag the CRM record
```

Reads the connected mailbox (`read-email/`) and regex-matches for literal
unsubscribe language ("unsubscribe", "remove me", "stop emailing", etc.) —
the same posture the SMS README states outright: **"Opt-outs are decided by
regex, never by a model."** A hit suppresses the address locally
(`store.suppress`) and tags every CRM record carrying that address "Do Not
Market", so the person is excluded from the *next SMS touch* too. This is a
guardrail, not the reply-classification engine the two-way design was
deliberately deferred on — run it on a schedule (daily, alongside `send`) so
an opt-out from last week doesn't get another email from this week's batch.

## Testing without sending anything

```bash
python src/email_agent/cli.py selftest
```

Zero network. Asserts content validation, deterministic rendering per record,
name hygiene, suppression round-tripping, and the touch-progression logic
(due/waiting/completed). Safe to run any time.

## Known gaps

- **BLOCKED: `email-integration`'s `send-email/` and `read-email/` both 500
  on this account, for every payload tried.** Verified live 2026-08-27
  against a connected mailbox (a Google Workspace / Gmail connection,
  `is_active: true`, access token valid, not expired). This is
  NOT a payload-shape problem on our side:
  - `to` as a plain string + `subject` + `body` (the documented-shape guess)
    passes DataSift's own serializer validation (confirmed by sending `to` as
    a LIST instead, which correctly 400s with `{"to":["Enter a valid email
    address."]}` — proving the endpoint validates `to` as a singular
    `EmailField` and our string shape is the right type) but still 500s past
    that point.
  - Every other content field name tried (`html_body`, `text`, `text_body`,
    `content`, `message`, `body_html`, `body_text`, `body`+`html` together,
    HTML content, a completely minimal `{to, subject}` with no content field
    at all) 500s identically.
  - **`read-email/` 500s too, called as a bare GET with no body and no query
    params at all.** There is no payload left to blame; a GET that carries
    nothing from us failing exactly like every POST variant is what rules out
    "wrong field name" as the cause.
  - The 500 response body is a bare Django default error page (no traceback,
    no JSON detail), so nothing more is diagnosable from this side.
  - **This reads as a platform-side bug or misconfiguration in DataSift's own
    email integration for this connected mailbox** (a scope gap between what
    Gmail granted — `gmail.modify` — and what DataSift's send/read code
    expects is one plausible cause, but unconfirmed without their server
    logs). Next step is DataSift support, not further guessing here:
    reproduce with `POST /api/internal/email-integration/187/send-email/`
    (also `GET .../read-email/`) and ask them to check what's throwing.
  - `mailer.send_email()`'s default payload (`{to, subject, body}`) is left
    as-is: it is very likely the RIGHT shape, just blocked by whatever is
    failing server-side. Re-run `doctor --test-send` after DataSift resolves
    it, or after reconnecting the mailbox, before assuming the code needs a
    change.
- **No bounce handling.** A hard-bounced address is not automatically
  suppressed in v1; it will just keep failing `send_email` and logging
  `status='failed'` in `store.sent`, which does not block future touches.
- **No reply reading beyond opt-outs.** A genuinely interested reply sits in
  the connected mailbox until a human checks it. That is the two-way engine
  the user explicitly deferred; revisit if this channel starts generating
  real reply volume — moot until `read-email/` itself works.
