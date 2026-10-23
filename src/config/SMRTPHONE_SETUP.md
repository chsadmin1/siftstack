# smrtPhone SMS Provider Setup Guide

## Overview

The Text Touch Builder sends SMS to leads on targeted SIFT boards through smrtPhone (https://phone.smrt.studio). It uses the same send call as the Python SMS agent in `src/sms_agent/smrtphone.py`. This guide covers configuration and testing.

## Prerequisites

- **smrtPhone account** with API access, and admin access to create an API token.
- **A smrtPhone number** on the account to send from.
- **Approved A2P 10DLC registration.** Carriers block business texts from numbers that are not registered. Until smrtPhone shows the registration as approved, sends may be accepted by the API but not delivered.

## Step 1: Get a smrtPhone API token

1. Log in to smrtPhone at https://phone.smrt.studio.
2. Go to **Admin > API Tokens** and create a token.
3. Copy the token. Treat it like a password.

## Step 2: Configure environment variables

Copy `.env.example` to `.env` if you have not already, then set:

```env
SMS_PROVIDER=smrtphone
SMRTPHONE_API_KEY=YOUR_API_TOKEN_HERE
SMS_FROM_NUMBER=+12085550100
# Optional; this is the default
SMRTPHONE_BASE=https://phone.smrt.studio
```

- `SMS_PROVIDER=smrtphone` selects the smrtPhone provider. Any other value, or none, uses the mock provider.
- `SMRTPHONE_API_KEY` is the token from Step 1. It is sent as the `X-Auth-smrtPhone` header.
- `SMS_FROM_NUMBER` is the smrtPhone number texts are sent from, in E.164 format.

## Step 3: Test the configuration

### Option A: Check the token without sending anything

A POST to `/sms/send` with no fields returns **400** "Missing required parameter(s)" on a valid token and **403** on an invalid one. Nothing is sent, because there is no destination.

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://phone.smrt.studio/sms/send \
  -H "X-Auth-smrtPhone: YOUR_API_TOKEN_HERE"
```

### Option B: Send one test text to a team phone

```bash
curl -X POST https://phone.smrt.studio/sms/send \
  -H "X-Auth-smrtPhone: YOUR_API_TOKEN_HERE" \
  -d "from=+12085550100" -d "to=+1TEAM_PHONE" -d "message=Text Touch test"
```

### Option C: Run the Text Touch endpoint

1. Start the development server:
   ```bash
   npm run dev
   ```
2. Send a test request:
   ```bash
   curl -X POST http://localhost:3000/api/touches/text/send \
     -H "Content-Type: application/json" \
     -d '{"boardId": "test-board-1", "messageTemplate": "Hi {firstName}, call us at 555-0123!"}'
   ```

The response lists each lead with `status: "sent"` or `"failed"` and the error for any failure.

## How It Works

1. The endpoint fetches the leads on the SIFT board.
2. Each lead's message is built from the template placeholders: `{firstName}`, `{lastName}`, `{phoneNumber}`, `{propertyAddress}`, `{propertyPrice}`.
3. The smrtPhone provider sends `POST {SMRTPHONE_BASE}/sms/send` with form fields `from`, `to` and `message`.
4. A 2xx answer is a sent message. The id comes from `smsId`, `id` or `sid` in the response when present.
5. Every attempt is written to the `text_touch_audit` table.

## Phone Number Format Requirements

Numbers must be E.164, for example `+12085550123`. A number without the leading `+` gets one added. Anything else is rejected before the API is called.

## Message Length Limits

The provider rejects a message over one SMS segment: 160 characters of plain text, or 70 if the message contains any non-ASCII character such as an emoji or a curly quote. Keep templates short, or remove that check in `SmrtPhoneSMSProvider` if multi-part texts are acceptable.

## Troubleshooting

- **"smrtPhone provider requires SMRTPHONE_API_KEY and SMS_FROM_NUMBER"**: one of the two is not set in `.env`.
- **HTTP 403**: the token is wrong or was revoked. Create a new one in Admin > API Tokens.
- **HTTP 400 naming a parameter**: the `from` number is not on the smrtPhone account, or a field is empty.
- **Sent, but the text never arrives**: check the A2P 10DLC registration status in smrtPhone.
- **"Invalid phone number format"**: the lead's phone number in the SIFT export is not E.164.

## Production Considerations

### Delivery status

smrtPhone posts SMS events to a webhook. The Python SMS agent receives them at `POST /hooks/{secret}/smrtphone`. Text Touch currently relies on the immediate API answer only.

### Rate limiting

smrtPhone does not publish a rate limit. Test with 5 to 10 leads first, and space out large campaigns. The SMS agent paces its own sends for the same reason.

### Compliance and opt-out

The Text Touch service does **not** enforce TCPA opt-in or opt-out checks. Make sure leads may be texted, honor every opt-out, and keep the audit log. smrtPhone's Do-Not-Text list (Admin > DNT Numbers) blocks numbers at the phone system.

### Audit log storage

All sends are logged to the database. Schedule regular backups:

```sql
SELECT * FROM text_touch_audit
WHERE sent_at >= DATE_TRUNC('month', CURRENT_DATE);
```

## Monitoring

```sql
SELECT campaign_id, COUNT(*) AS total,
       SUM(CASE WHEN status='sent' THEN 1 ELSE 0 END) AS sent,
       SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed
FROM text_touch_audit
WHERE sent_at >= NOW() - INTERVAL '24 hours'
GROUP BY campaign_id;
```

Watch for a spike in failed sends, which usually means a token or A2P problem, and for invalid numbers, which point at the SIFT export format.
