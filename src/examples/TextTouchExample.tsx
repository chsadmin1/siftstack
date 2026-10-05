/**
 * Text Touch Builder Integration Example
 *
 * Shows how to integrate the TextTouchBuilder component into your application.
 * Includes both the campaign builder and audit log display.
 */

import React from 'react';
import { TextTouchBuilder } from '../components/TextTouchBuilder';
import { TextTouchAuditLog } from '../components/TextTouchAuditLog';

/**
 * Full-page example with builder and audit log
 */
export function TextTouchPage() {
  const [campaignId, setCampaignId] = React.useState<string | undefined>();

  return (
    <div className="min-h-screen bg-gray-100">
      {/* Header */}
      <header className="bg-white shadow">
        <div className="max-w-4xl mx-auto px-6 py-4">
          <h1 className="text-2xl font-bold text-gray-900">Text Touch Campaigns</h1>
          <p className="text-gray-600 mt-1">
            Build and execute SMS campaigns targeting SIFT board leads
          </p>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-4xl mx-auto py-8 px-6">
        {/* Campaign Builder Section */}
        <section className="mb-8">
          <TextTouchBuilder />
        </section>

        {/* Audit Log Section */}
        <section className="bg-white rounded-lg shadow-md p-6">
          <h2 className="text-xl font-bold mb-4">Campaign Audit Log</h2>
          <TextTouchAuditLog campaignId={campaignId} limit={20} />
        </section>

        {/* Integration Notes */}
        <section className="mt-8 bg-blue-50 border border-blue-200 rounded-lg p-6">
          <h3 className="font-bold text-blue-900 mb-2">Integration Notes</h3>
          <ul className="text-sm text-blue-800 space-y-1 list-disc list-inside">
            <li>Ensure SMS provider credentials are set in environment variables</li>
            <li>SIFT board API endpoint must be available at /api/sift/boards</li>
            <li>Database schema must include text_touch_audit table</li>
            <li>User authentication/authorization required for production use</li>
            <li>Phone number validation recommended before sending</li>
            <li>Consider implementing opt-in/opt-out compliance checks</li>
          </ul>
        </section>
      </main>
    </div>
  );
}

/**
 * Minimal example - just the builder
 */
export function TextTouchBuilderOnly() {
  return <TextTouchBuilder />;
}

/**
 * API Usage Examples
 */
export const apiExamples = {
  /**
   * Example 1: Send campaign via fetch
   */
  sendCampaign: async () => {
    const response = await fetch('/api/touches/text/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        boardId: 'board-1',
        messageTemplate: 'Hi {firstName}, check out this property: {propertyAddress}',
        sendAt: 'immediate',
      }),
    });

    const result = await response.json();
    console.log(`Campaign sent: ${result.summary.sent} messages`);
    console.log(`Failed: ${result.summary.failed}`);
  },

  /**
   * Example 2: Using the hook directly
   */
  useHookExample: () => {
    // import { useTextTouch } from '../hooks/useTextTouch';
    //
    // function MyComponent() {
    //   const {
    //     boards,
    //     selectedBoardId,
    //     messageTemplate,
    //     preview,
    //     isLoading,
    //     error,
    //     selectBoard,
    //     updateMessage,
    //     sendCampaign,
    //   } = useTextTouch();
    //
    //   return (
    //     // Your custom UI here
    //   );
    // }
  },

  /**
   * Example 3: Using the service directly
   */
  useServiceExample: async () => {
    // import { executeCampaign } from '../services/textTouch';
    //
    // const summary = await executeCampaign(
    //   'campaign-001',
    //   'board-1',
    //   'Hi {firstName}, we have a property for you!',
    //   'user@example.com'
    // );
    //
    // console.log(`Sent to ${summary.sent} leads, ${summary.failed} failed`);
  },
};

/**
 * Configuration Guide
 */
export const configurationGuide = `
# Text Touch Builder Configuration

## Environment Variables

Configure your SMS provider in .env:

### Twilio
SMS_PROVIDER=twilio
SMS_ACCOUNT_ID=your_twilio_account_sid
SMS_AUTH_TOKEN=your_twilio_auth_token
SMS_FROM_NUMBER=+1234567890

### AWS SNS
SMS_PROVIDER=aws-sns
SMS_ACCOUNT_ID=your_aws_account_id
SMS_AUTH_TOKEN=your_aws_secret_key
SMS_FROM_NUMBER=+1234567890

### Development/Testing
SMS_PROVIDER=mock
# Mock provider simulates 95% success rate for testing

## Database Setup

Run this migration to add the audit table:

\`\`\`sql
CREATE TABLE text_touch_audit (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  campaign_id VARCHAR(255) NOT NULL,
  lead_id VARCHAR(255) NOT NULL,
  phone_number VARCHAR(20) NOT NULL,
  message_sent TEXT NOT NULL,
  sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  status VARCHAR(50) NOT NULL DEFAULT 'sent',
  error TEXT,
  sms_provider VARCHAR(100),
  provider_response_id VARCHAR(255),
  created_by VARCHAR(255),
  board_id VARCHAR(255) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_campaign_id (campaign_id),
  INDEX idx_lead_id (lead_id),
  INDEX idx_sent_at (sent_at),
  INDEX idx_board_id (board_id)
);
\`\`\`

## API Requirements

### SIFT Board API
Endpoint: GET /api/sift/boards
Response: Array of { id, name, leadCount, description }

### Lead Fetching
Current implementation uses mock data. To integrate with real boards:
1. Update fetchLeadsFromBoard() in src/services/textTouch.ts
2. Call your actual SIFT board API endpoint
3. Return array with { id, firstName, lastName, phoneNumber }

## Production Checklist

- [ ] SMS provider credentials set in environment
- [ ] Database audit table created
- [ ] SIFT board API endpoint available
- [ ] User authentication implemented
- [ ] Phone number validation enabled
- [ ] Opt-in/opt-out compliance checks added
- [ ] Audit logging tested
- [ ] Error handling reviewed
- [ ] Rate limiting configured
- [ ] Monitoring/alerting set up
`;

export default TextTouchPage;
