# Kixie SMS Provider Setup Guide

## Overview

The Text Touch Builder uses Kixie (https://app.kixie.com/) to send SMS messages to leads on targeted SIFT boards. This guide walks through configuration and testing.

## Prerequisites

- **Kixie Account** with Professional Billing Tier or higher (API access requires paid tier)
- **Admin Access** to Kixie Dashboard to retrieve API credentials
- **Support Activation** — Kixie support must activate your API key before use (contact support@kixie.com)

## Step 1: Get Kixie API Credentials

1. Log in to Kixie Dashboard: https://app.kixie.com/
2. Navigate to: **Manage > Account Settings > Integrations**
3. You will see:
   - **API Key** — Copy this value
   - **Business ID** — Copy this value
4. Contact support@kixie.com if you don't see these fields
   - Mention: "Please activate API access for my Business ID"
   - API requires Professional+ Billing Tier

## Step 2: Configure Environment Variables

Copy `.env.example` to `.env` (if not already done):

```bash
cp .env.example .env
```

Add or update the Kixie SMS configuration in `.env`:

```env
# SMS Provider Configuration
SMS_PROVIDER=kixie
KIXIE_API_KEY=YOUR_API_KEY_HERE
KIXIE_BUSINESS_ID=YOUR_BUSINESS_ID_HERE
KIXIE_EMAIL=your_email@creativehomesolutions.org
```

**Field Descriptions:**
- `SMS_PROVIDER=kixie` — Enables Kixie SMS provider
- `KIXIE_API_KEY` — Your API key from Kixie Dashboard
- `KIXIE_BUSINESS_ID` — Your Business ID from Kixie Dashboard
- `KIXIE_EMAIL` — Email address for Kixie API requests (for logging/audit purposes)

## Step 3: Test Configuration

### Option A: Direct API Test

Test the Kixie API endpoint directly:

```bash
curl -X POST https://apig.kixie.com/app/event \
  -H "Content-Type: application/json" \
  -d '{
    "businessid": "YOUR_BUSINESS_ID",
    "apikey": "YOUR_API_KEY",
    "target": "+15551234567",
    "eventname": "sms",
    "message": "Test message from CHS Text Touch Builder",
    "email": "your_email@creativehomesolutions.org"
  }'
```

Expected success response:
```json
{
  "success": true,
  "result": {
    "onCall": 0,
    "registered": 1,
    "device": "Device ID"
  }
}
```

### Option B: Run Text Touch API Test

1. Start the development server:
   ```bash
   npm run dev
   ```

2. Make a test request to the text touch endpoint:
   ```bash
   curl -X POST http://localhost:3000/api/touches/text/send \
     -H "Content-Type: application/json" \
     -d '{
       "boardId": "test-board-1",
       "messageTemplate": "Hi {firstName}, call us at 555-0123!"
     }'
   ```

Expected response:
```json
{
  "campaignId": "camp-1728110400123",
  "boardId": "test-board-1",
  "sent": 5,
  "failed": 0,
  "pending": 0,
  "results": [
    {
      "campaignId": "camp-1728110400123",
      "leadId": "lead-1",
      "phoneNumber": "+15551234567",
      "sentAt": "2026-10-05T14:30:00.123Z",
      "status": "sent"
    }
    // ... more results
  ],
  "totalAttempted": 5,
  "executedAt": "2026-10-05T14:30:00.123Z"
}
```

## How It Works

### Message Flow

1. **Campaign Submission**
   - User selects SIFT board and composes message via TextTouchBuilder UI
   - Message template supports placeholders: `{firstName}`, `{lastName}`, `{phoneNumber}`, etc.
   - Confirmation modal shows lead count before send

2. **Message Processing**
   - Leads are fetched from the specified SIFT board
   - Message template is substituted with lead data
   - Phone numbers are validated (E.164 format required)
   - Message length is checked (max 160 chars for GSM text, 70 for Unicode)

3. **SMS Sending via Kixie**
   - Each message is sent to Kixie API: `POST https://apig.kixie.com/app/event`
   - Kixie responds with success/failure status
   - Each result is logged to audit table

4. **Audit Logging**
   - All sends are logged to `text_touch_audit` table
   - Tracks: campaign ID, lead ID, phone number, message content, status, timestamp, Kixie response
   - Enables compliance tracking and troubleshooting

### API Request Format

The service sends to Kixie in this format:

```json
{
  "businessid": "YOUR_BUSINESS_ID",
  "apikey": "YOUR_API_KEY",
  "target": "+14155550123",
  "eventname": "sms",
  "message": "Your message content here",
  "email": "your_email@creativehomesolutions.org"
}
```

**Field Details:**
- `businessid` — Your Kixie Business ID
- `apikey` — Your Kixie API Key
- `target` — Recipient phone number in E.164 format (e.g., `+14155550123`)
- `eventname` — Set to `"sms"` for text messages
- `message` — SMS text (max 160 GSM chars, max 70 Unicode chars; auto-split if longer)
- `email` — Email for audit logging in Kixie

## Phone Number Format Requirements

Kixie requires **E.164 International Format:**

- Starts with `+` symbol
- Country code (1-3 digits) + subscriber number
- Maximum 15 total digits
- No spaces, hyphens, or special characters

**Examples:**
- ✅ `+14155550123` (US)
- ✅ `+447911123456` (UK)
- ✅ `+33123456789` (France)
- ❌ `(415) 555-0123` (formatted US)
- ❌ `415-555-0123` (formatted US)
- ❌ `4155550123` (no country code)

**SIFT Integration Note:**
Update your SIFT board's lead export to include phone numbers in E.164 format, or the Text Touch service will need to normalize them. Currently uses mock data for testing — integrate with actual SIFT API when available.

## Message Length Limits

Kixie applies standard SMS limits:

| Character Set | Max Length | Notes |
|---------------|-----------|-------|
| GSM (ASCII) | 160 chars | Standard text, numbers, punctuation |
| Unicode | 70 chars | Any Unicode character triggers 70-char limit |

**Example:**
- ✅ `"Hi John! Call us today!"` = 26 chars (GSM)
- ✅ `"Hola! ¿Cómo estás?"` = 20 chars (Unicode, uses 70-char limit)
- ❌ `"This is a very long message that exceeds 160 characters and will fail validation in the API..."` (too long)

Messages exceeding limits are **rejected by the API** — the service validates before sending.

## Troubleshooting

### Problem: "API key not activated"

**Solution:** Contact support@kixie.com with your Business ID and request API key activation.

### Problem: "Invalid phone number format"

**Solution:** Ensure phone numbers are in E.164 format (`+{country}{number}`). The service will reject improperly formatted numbers.

### Problem: "Message too long"

**Solution:** Limit messages to 160 GSM characters. If using non-ASCII characters, limit to 70 characters. Remove unnecessary text or use shorter placeholders.

### Problem: "SMS Provider Configuration Missing"

**Check:**
1. `.env` file exists and has `SMS_PROVIDER=kixie`
2. `KIXIE_API_KEY` is set
3. `KIXIE_BUSINESS_ID` is set
4. Application was restarted after `.env` changes

### Problem: "Failed to send to Kixie API"

**Check:**
1. Network connectivity to `https://apig.kixie.com/`
2. API credentials are correct (copy-paste into curl test above)
3. Kixie support has activated API access for your account
4. Business Billing Tier is Professional or higher

## Production Considerations

### Webhooks (Optional)

Kixie can send delivery status updates via webhooks. To set up:

1. Kixie Dashboard > Webhooks > Create New Webhook
2. Configure URL for delivery status callbacks (e.g., `/api/touches/text/webhook`)
3. Implement webhook handler to update audit table with delivery confirmation

**Currently:** The service uses the immediate API response only. Webhook integration can be added later for enhanced delivery tracking.

### Rate Limiting

Kixie does not publicly document rate limits. For high-volume campaigns:
- Test with small campaign sizes first (5-10 leads)
- Contact support@kixie.com for rate limit details
- Consider implementing request queuing for large campaigns (1000+ leads)

### Compliance & Opt-Out

The Text Touch service does **not** currently enforce TCPA opt-in/opt-out checks. Ensure:
- Leads have opted in to SMS marketing
- Comply with local regulations (TCPA, GDPR, etc.)
- Maintain audit logs for legal compliance

**Recommendation:** Integrate with a compliance service or maintain a manual opt-out list before running production campaigns.

### Audit Log Storage

All sends are logged to the database. Schedule regular backups and archival:

```sql
-- Export audit log to CSV (monthly)
SELECT * FROM text_touch_audit 
WHERE sent_at >= DATE_TRUNC('month', CURRENT_DATE)
INTO OUTFILE '/path/to/archive/text_touch_audit_YYYY_MM.csv';
```

## Support & Resources

- **Kixie Developer Docs:** https://www.kixie.com/developer/
- **SMS API Documentation:** https://www.kixie.com/developer/automation-send-sms/
- **Support Center:** https://support.kixie.com/
- **Email Support:** support@kixie.com

## Monitoring

Check the audit table for send status:

```sql
-- View recent sends
SELECT campaign_id, COUNT(*) as total, 
       SUM(CASE WHEN status='sent' THEN 1 ELSE 0 END) as sent,
       SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed
FROM text_touch_audit
WHERE sent_at >= NOW() - INTERVAL '24 hours'
GROUP BY campaign_id
ORDER BY sent_at DESC;
```

Monitor for:
- Sudden spike in failed sends (check Kixie API status)
- Invalid phone numbers (check SIFT data export format)
- Provider errors (check .env configuration)

---

**Last Updated:** 2026-10-05  
**Text Touch Builder Version:** 1.0.0
