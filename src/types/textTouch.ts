/**
 * Type Definitions for Text Touch Campaign System
 *
 * Supports building and executing SMS campaigns targeting leads on SIFT boards.
 */

/**
 * Represents a SIFT board (lead list/segment)
 */
export interface SiftBoard {
  id: string;
  name: string;
  leadCount: number;
  description?: string;
  createdAt?: string;
}

/**
 * Represents a text message campaign configuration
 */
export interface TextTouchCampaign {
  id?: string;
  boardId: string;
  messageTemplate: string;
  sendAt?: string; // ISO timestamp or "immediate"
  scheduledFor?: Date;
  createdBy: string;
  status?: 'draft' | 'scheduled' | 'sending' | 'sent' | 'failed';
  createdAt?: string;
  updatedAt?: string;
}

/**
 * Represents the result of sending a single text message
 */
export interface TextTouchResult {
  campaignId: string;
  leadId: string;
  phoneNumber: string;
  sentAt: string; // ISO timestamp
  status: 'sent' | 'failed' | 'pending';
  error?: string;
}

/**
 * Summary of campaign execution results
 */
export interface TextTouchExecutionSummary {
  campaignId: string;
  boardId: string;
  sent: number;
  failed: number;
  pending: number;
  results: TextTouchResult[];
  executedAt: string; // ISO timestamp
  totalAttempted: number;
}

/**
 * Request payload for sending text campaign
 */
export interface SendTextTouchRequest {
  boardId: string;
  messageTemplate: string;
  sendAt?: string; // "immediate" or ISO timestamp
}

/**
 * Response payload from send endpoint
 */
export interface SendTextTouchResponse {
  success: boolean;
  campaignId: string;
  summary: TextTouchExecutionSummary;
  error?: string;
}
