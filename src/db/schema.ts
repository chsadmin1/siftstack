/**
 * Database Schema Definitions
 *
 * TypeScript type definitions for database tables.
 *
 * TODO: Implement actual schema in database migration/setup files
 * This file serves as documentation and type definitions.
 */

/**
 * TextTouchAudit Table Schema
 *
 * Tracks all text message sends for compliance and auditing.
 *
 * SQL:
 * CREATE TABLE text_touch_audit (
 *   id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 *   campaign_id VARCHAR(255) NOT NULL,
 *   lead_id VARCHAR(255) NOT NULL,
 *   phone_number VARCHAR(20) NOT NULL,
 *   message_sent TEXT NOT NULL,
 *   sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 *   status VARCHAR(50) NOT NULL DEFAULT 'sent',
 *   error TEXT,
 *   sms_provider VARCHAR(100),
 *   provider_response_id VARCHAR(255),
 *   created_by VARCHAR(255),
 *   board_id VARCHAR(255),
 *   created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 *   updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 *   INDEX idx_campaign_id (campaign_id),
 *   INDEX idx_lead_id (lead_id),
 *   INDEX idx_sent_at (sent_at),
 *   INDEX idx_board_id (board_id)
 * );
 */
export interface TextTouchAuditRecord {
  id: string; // UUID
  campaignId: string;
  leadId: string;
  phoneNumber: string;
  messageSent: string;
  sentAt: string; // ISO timestamp
  status: 'sent' | 'failed' | 'pending' | 'bounced';
  error?: string;
  smsProvider?: string; // e.g., 'twilio', 'aws-sns'
  providerResponseId?: string; // Reference ID from SMS provider
  createdBy?: string; // User who initiated campaign
  boardId: string;
  createdAt: string; // ISO timestamp
  updatedAt: string; // ISO timestamp
}

/**
 * Helper type for inserting new audit records (without id/timestamps)
 */
export type TextTouchAuditInsert = Omit<TextTouchAuditRecord, 'id' | 'createdAt' | 'updatedAt'>;

/**
 * Schema migration helper
 *
 * To apply this schema to your database, run:
 *
 * PostgreSQL:
 * ```sql
 * CREATE TABLE IF NOT EXISTS text_touch_audit (
 *   id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 *   campaign_id VARCHAR(255) NOT NULL,
 *   lead_id VARCHAR(255) NOT NULL,
 *   phone_number VARCHAR(20) NOT NULL,
 *   message_sent TEXT NOT NULL,
 *   sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 *   status VARCHAR(50) NOT NULL DEFAULT 'sent',
 *   error TEXT,
 *   sms_provider VARCHAR(100),
 *   provider_response_id VARCHAR(255),
 *   created_by VARCHAR(255),
 *   board_id VARCHAR(255) NOT NULL,
 *   created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 *   updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 *   INDEX idx_campaign_id (campaign_id),
 *   INDEX idx_lead_id (lead_id),
 *   INDEX idx_sent_at (sent_at),
 *   INDEX idx_board_id (board_id)
 * );
 * ```
 *
 * MySQL:
 * ```sql
 * CREATE TABLE IF NOT EXISTS text_touch_audit (
 *   id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
 *   campaign_id VARCHAR(255) NOT NULL,
 *   lead_id VARCHAR(255) NOT NULL,
 *   phone_number VARCHAR(20) NOT NULL,
 *   message_sent LONGTEXT NOT NULL,
 *   sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 *   status VARCHAR(50) NOT NULL DEFAULT 'sent',
 *   error TEXT,
 *   sms_provider VARCHAR(100),
 *   provider_response_id VARCHAR(255),
 *   created_by VARCHAR(255),
 *   board_id VARCHAR(255) NOT NULL,
 *   created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 *   updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 *   KEY idx_campaign_id (campaign_id),
 *   KEY idx_lead_id (lead_id),
 *   KEY idx_sent_at (sent_at),
 *   KEY idx_board_id (board_id)
 * );
 * ```
 */
export const textTouchAuditSchema = {
  table: 'text_touch_audit',
  columns: {
    id: { type: 'uuid', primary: true, default: 'gen_random_uuid()' },
    campaignId: { type: 'varchar(255)', notNull: true },
    leadId: { type: 'varchar(255)', notNull: true },
    phoneNumber: { type: 'varchar(20)', notNull: true },
    messageSent: { type: 'text', notNull: true },
    sentAt: { type: 'timestamp', default: 'CURRENT_TIMESTAMP' },
    status: { type: 'varchar(50)', notNull: true, default: 'sent' },
    error: { type: 'text', nullable: true },
    smsProvider: { type: 'varchar(100)', nullable: true },
    providerResponseId: { type: 'varchar(255)', nullable: true },
    createdBy: { type: 'varchar(255)', nullable: true },
    boardId: { type: 'varchar(255)', notNull: true },
    createdAt: { type: 'timestamp', default: 'CURRENT_TIMESTAMP' },
    updatedAt: { type: 'timestamp', default: 'CURRENT_TIMESTAMP' },
  },
  indexes: [
    { name: 'idx_campaign_id', columns: ['campaignId'] },
    { name: 'idx_lead_id', columns: ['leadId'] },
    { name: 'idx_sent_at', columns: ['sentAt'] },
    { name: 'idx_board_id', columns: ['boardId'] },
  ],
};
