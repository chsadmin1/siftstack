/**
 * Text Touch Service
 *
 * Core business logic for text message campaigns:
 * - Fetch leads from SIFT board
 * - Template substitution
 * - SMS provider integration
 * - Audit logging
 */

import {
  TextTouchResult,
  TextTouchExecutionSummary,
  SiftBoard
} from '../types/textTouch';
import { TextTouchAuditInsert, TextTouchAuditRecord } from '../db/schema';

/**
 * SMS Provider Configuration
 *
 * Expects environment variables:
 * - SMS_PROVIDER: 'smrtphone' | 'twilio' | 'aws-sns' | 'mock'
 * - For smrtPhone:
 *   - SMRTPHONE_API_KEY: Admin > API Tokens in the smrtPhone web app
 *   - SMRTPHONE_BASE: optional, defaults to https://phone.smrt.studio
 * - For Twilio:
 *   - SMS_ACCOUNT_ID: Twilio Account SID
 *   - SMS_AUTH_TOKEN: Twilio Auth Token
 * - SMS_FROM_NUMBER: Sender phone number (required for smrtPhone and Twilio)
 */
interface SMSProvider {
  name: string;
  send(phoneNumber: string, message: string): Promise<{ success: boolean; messageId?: string; error?: string }>;
}

/**
 * Mock SMS Provider (for testing)
 */
class MockSMSProvider implements SMSProvider {
  name = 'mock';

  async send(phoneNumber: string, message: string): Promise<{ success: boolean; messageId?: string; error?: string }> {
    // Simulate 95% success rate
    const isSuccess = Math.random() > 0.05;

    if (isSuccess) {
      return {
        success: true,
        messageId: `mock-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
      };
    } else {
      return {
        success: false,
        error: 'Mock provider: simulated send failure',
      };
    }
  }
}

/**
 * smrtPhone SMS Provider
 *
 * Sends SMS through smrtPhone's API, the same call the Python SMS agent makes
 * (src/sms_agent/smrtphone.py): POST {SMRTPHONE_BASE}/sms/send with form
 * fields from / to / message and the X-Auth-smrtPhone header.
 * Requires: SMRTPHONE_API_KEY, SMS_FROM_NUMBER (a smrtPhone number on the account)
 * Texts only deliver once smrtPhone's A2P 10DLC registration is approved.
 */
class SmrtPhoneSMSProvider implements SMSProvider {
  name = 'smrtphone';
  private apiKey: string;
  private fromNumber: string;
  private endpoint: string;

  constructor(apiKey: string, fromNumber: string, baseUrl: string) {
    if (!apiKey || !fromNumber) {
      throw new Error('smrtPhone provider requires SMRTPHONE_API_KEY and SMS_FROM_NUMBER');
    }
    this.apiKey = apiKey;
    this.fromNumber = fromNumber.startsWith('+') ? fromNumber : `+${fromNumber}`;
    this.endpoint = `${baseUrl.replace(/\/+$/, '')}/sms/send`;
  }

  async send(phoneNumber: string, message: string): Promise<{ success: boolean; messageId?: string; error?: string }> {
    try {
      // Validate phone number format (E.164)
      if (!phoneNumber.match(/^\+?[1-9]\d{1,14}$/)) {
        return {
          success: false,
          error: `Invalid phone number format. Expected E.164 format (e.g., +14155550123), got: ${phoneNumber}`,
        };
      }

      // Ensure phone number is in E.164 format
      const normalizedPhone = phoneNumber.startsWith('+') ? phoneNumber : `+${phoneNumber}`;

      // Validate message length (160 chars for GSM, 70 for Unicode)
      const hasUnicode = /[^\x00-\x7F]/.test(message);
      const maxLength = hasUnicode ? 70 : 160;

      if (message.length > maxLength) {
        return {
          success: false,
          error: `Message too long. Max ${maxLength} characters for ${hasUnicode ? 'Unicode' : 'GSM'} text, got ${message.length}`,
        };
      }

      const response = await fetch(this.endpoint, {
        method: 'POST',
        headers: {
          'X-Auth-smrtPhone': this.apiKey,
          'Accept': 'application/json',
          'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({ from: this.fromNumber, to: normalizedPhone, message }).toString(),
      });

      if (!response.ok) {
        const detail = (await response.text()).slice(0, 200);
        return {
          success: false,
          error: `smrtPhone API error: ${response.status} ${response.statusText} ${detail}`.trim(),
        };
      }

      // smrtPhone answers 2xx with the message id under smsId, id or sid.
      let messageId = '';
      try {
        const result = await response.json();
        messageId = String(result?.smsId || result?.id || result?.sid || '');
      } catch {
        // A 2xx with no JSON body is still an accepted send.
      }
      return {
        success: true,
        messageId: messageId || `smrtphone-${Date.now()}-${normalizedPhone}`,
      };
    } catch (error) {
      return {
        success: false,
        error: error instanceof Error ? error.message : 'Unknown smrtPhone provider error',
      };
    }
  }
}

/**
 * Twilio SMS Provider
 *
 * TODO: Implement actual Twilio integration
 */
class TwilioSMSProvider implements SMSProvider {
  name = 'twilio';
  private accountSid: string;
  private authToken: string;
  private fromNumber: string;

  constructor(accountSid: string, authToken: string, fromNumber: string) {
    this.accountSid = accountSid;
    this.authToken = authToken;
    this.fromNumber = fromNumber;
  }

  async send(phoneNumber: string, message: string): Promise<{ success: boolean; messageId?: string; error?: string }> {
    try {
      // TODO: Implement Twilio API call
      // const twilio = require('twilio');
      // const client = twilio(this.accountSid, this.authToken);
      // const result = await client.messages.create({
      //   body: message,
      //   from: this.fromNumber,
      //   to: phoneNumber,
      // });
      // return { success: true, messageId: result.sid };

      return {
        success: false,
        error: 'Twilio provider not yet implemented',
      };
    } catch (error) {
      return {
        success: false,
        error: error instanceof Error ? error.message : 'Unknown Twilio error',
      };
    }
  }
}

/**
 * Get configured SMS provider
 */
function getSMSProvider(): SMSProvider {
  const provider = process.env.SMS_PROVIDER || 'mock';

  switch (provider) {
    case 'smrtphone':
      return new SmrtPhoneSMSProvider(
        process.env.SMRTPHONE_API_KEY || '',
        process.env.SMS_FROM_NUMBER || '',
        process.env.SMRTPHONE_BASE || 'https://phone.smrt.studio'
      );
    case 'twilio':
      return new TwilioSMSProvider(
        process.env.SMS_ACCOUNT_ID || '',
        process.env.SMS_AUTH_TOKEN || '',
        process.env.SMS_FROM_NUMBER || ''
      );
    case 'aws-sns':
      // TODO: Implement AWS SNS provider
      console.warn('AWS SNS provider not yet implemented, falling back to mock');
      return new MockSMSProvider();
    case 'mock':
    default:
      return new MockSMSProvider();
  }
}

/**
 * Substitute template placeholders with lead data
 *
 * Supported placeholders:
 * - {firstName}, {lastName}, {phoneNumber}, {propertyAddress}, {propertyPrice}
 */
export function substituteMessage(
  template: string,
  leadData: Record<string, string | number>
): string {
  let message = template;

  // Replace all placeholder patterns
  Object.entries(leadData).forEach(([key, value]) => {
    const pattern = new RegExp(`\\{${key}\\}`, 'g');
    message = message.replace(pattern, String(value));
  });

  // Remove any unreplaced placeholders (safety)
  message = message.replace(/\{[^}]+\}/g, '');

  return message;
}

/**
 * Fetch leads from a SIFT board
 *
 * TODO: Integrate with actual SIFT API
 * For now, returns mock data
 */
export async function fetchLeadsFromBoard(boardId: string): Promise<
  Array<{ id: string; firstName: string; lastName: string; phoneNumber: string }>
> {
  // Mock data for testing
  const mockLeads = [
    { id: 'lead-1', firstName: 'John', lastName: 'Doe', phoneNumber: '+15551234567' },
    { id: 'lead-2', firstName: 'Jane', lastName: 'Smith', phoneNumber: '+15559876543' },
    { id: 'lead-3', firstName: 'Bob', lastName: 'Johnson', phoneNumber: '+15552468135' },
    { id: 'lead-4', firstName: 'Alice', lastName: 'Williams', phoneNumber: '+15559999999' },
    { id: 'lead-5', firstName: 'Charlie', lastName: 'Brown', phoneNumber: '+15553333333' },
  ];

  // TODO: Replace with actual SIFT API call
  // const response = await fetch(`/api/sift/boards/${boardId}/leads`);
  // return await response.json();

  return mockLeads;
}

/**
 * Log results to audit table
 *
 * TODO: Implement actual database insert
 */
export async function logToAudit(
  records: TextTouchAuditInsert[]
): Promise<TextTouchAuditRecord[]> {
  // TODO: Insert to database
  // const db = getDatabase();
  // return db.query('INSERT INTO text_touch_audit (...) VALUES (...)');

  console.log(`[AUDIT] Logging ${records.length} text touch records`);
  return records.map((r, i) => ({
    ...r,
    id: `audit-${Date.now()}-${i}`,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  }));
}

/**
 * Execute a text touch campaign
 *
 * Main orchestration function:
 * 1. Fetch leads from board
 * 2. Validate board and leads exist
 * 3. Send SMS to each lead
 * 4. Log results to audit table
 * 5. Return summary
 */
export async function executeCampaign(
  campaignId: string,
  boardId: string,
  messageTemplate: string,
  createdBy?: string
): Promise<TextTouchExecutionSummary> {
  const executionStartTime = Date.now();
  const results: TextTouchResult[] = [];
  const auditRecords: TextTouchAuditInsert[] = [];

  let sent = 0;
  let failed = 0;
  let pending = 0;

  try {
    // Fetch leads from SIFT board
    const leads = await fetchLeadsFromBoard(boardId);

    if (!leads || leads.length === 0) {
      throw new Error(`No leads found for board: ${boardId}`);
    }

    const smsProvider = getSMSProvider();

    // Process each lead
    for (const lead of leads) {
      // Validate phone number
      if (!lead.phoneNumber || lead.phoneNumber.trim() === '') {
        failed++;
        results.push({
          campaignId,
          leadId: lead.id,
          phoneNumber: '',
          sentAt: new Date().toISOString(),
          status: 'failed',
        });
        auditRecords.push({
          campaignId,
          leadId: lead.id,
          phoneNumber: '',
          messageSent: '',
          sentAt: new Date().toISOString(),
          status: 'failed',
          error: 'Missing phone number',
          boardId,
          createdBy,
        });
        continue;
      }

      // Substitute message template
      const substitutedMessage = substituteMessage(messageTemplate, {
        firstName: lead.firstName,
        lastName: lead.lastName,
        phoneNumber: lead.phoneNumber,
      });

      // Send via SMS provider
      const sendResult = await smsProvider.send(lead.phoneNumber, substitutedMessage);

      if (sendResult.success) {
        sent++;
        results.push({
          campaignId,
          leadId: lead.id,
          phoneNumber: lead.phoneNumber,
          sentAt: new Date().toISOString(),
          status: 'sent',
        });
        auditRecords.push({
          campaignId,
          leadId: lead.id,
          phoneNumber: lead.phoneNumber,
          messageSent: substitutedMessage,
          sentAt: new Date().toISOString(),
          status: 'sent',
          smsProvider: smsProvider.name,
          providerResponseId: sendResult.messageId,
          boardId,
          createdBy,
        });
      } else {
        failed++;
        results.push({
          campaignId,
          leadId: lead.id,
          phoneNumber: lead.phoneNumber,
          sentAt: new Date().toISOString(),
          status: 'failed',
          error: sendResult.error,
        });
        auditRecords.push({
          campaignId,
          leadId: lead.id,
          phoneNumber: lead.phoneNumber,
          messageSent: substitutedMessage,
          sentAt: new Date().toISOString(),
          status: 'failed',
          error: sendResult.error,
          boardId,
          createdBy,
        });
      }
    }

    // Log all results to audit table
    await logToAudit(auditRecords);

    // Return execution summary
    return {
      campaignId,
      boardId,
      sent,
      failed,
      pending,
      results,
      executedAt: new Date().toISOString(),
      totalAttempted: leads.length,
    };

  } catch (error) {
    throw new Error(
      `Campaign execution failed: ${error instanceof Error ? error.message : 'Unknown error'}`
    );
  }
}
