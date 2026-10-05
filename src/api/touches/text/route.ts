/**
 * Text Touch Campaign API Endpoint
 *
 * POST /api/touches/text/send
 *
 * Accepts campaign configuration and executes text message campaign.
 * Returns execution summary with delivery results and audit logging.
 */

import { SendTextTouchRequest, SendTextTouchResponse } from '../../../types/textTouch';
import { executeCampaign } from '../../../services/textTouch';

/**
 * POST /api/touches/text/send
 *
 * Request body:
 * {
 *   "boardId": "board-1",
 *   "messageTemplate": "Hi {firstName}, we found a property match for you!",
 *   "sendAt": "immediate" | ISO timestamp
 * }
 *
 * Response:
 * {
 *   "success": true,
 *   "campaignId": "campaign-xyz",
 *   "summary": {
 *     "sent": 45,
 *     "failed": 2,
 *     "pending": 0,
 *     "results": [...],
 *     "executedAt": "2026-10-05T13:30:00Z",
 *     "totalAttempted": 47
 *   }
 * }
 */
export async function POST(req: Request): Promise<Response> {
  try {
    const body: SendTextTouchRequest = await req.json();

    // Validate required fields
    if (!body.boardId || !body.boardId.trim()) {
      return Response.json({
        success: false,
        campaignId: '',
        summary: {
          campaignId: '',
          boardId: '',
          sent: 0,
          failed: 0,
          pending: 0,
          results: [],
          executedAt: new Date().toISOString(),
          totalAttempted: 0,
        },
        error: 'boardId is required',
      } as SendTextTouchResponse, { status: 400 });
    }

    if (!body.messageTemplate || !body.messageTemplate.trim()) {
      return Response.json({
        success: false,
        campaignId: '',
        summary: {
          campaignId: '',
          boardId: body.boardId,
          sent: 0,
          failed: 0,
          pending: 0,
          results: [],
          executedAt: new Date().toISOString(),
          totalAttempted: 0,
        },
        error: 'messageTemplate is required',
      } as SendTextTouchResponse, { status: 400 });
    }

    if (body.messageTemplate.trim().length < 10) {
      return Response.json({
        success: false,
        campaignId: '',
        summary: {
          campaignId: '',
          boardId: body.boardId,
          sent: 0,
          failed: 0,
          pending: 0,
          results: [],
          executedAt: new Date().toISOString(),
          totalAttempted: 0,
        },
        error: 'messageTemplate must be at least 10 characters',
      } as SendTextTouchResponse, { status: 400 });
    }

    // Generate campaign ID
    const campaignId = `campaign-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

    // Get current user (TODO: extract from auth context)
    const createdBy = 'system'; // TODO: get from request headers or auth

    // Execute campaign
    const summary = await executeCampaign(
      campaignId,
      body.boardId,
      body.messageTemplate,
      createdBy
    );

    // Return success response
    return Response.json({
      success: true,
      campaignId,
      summary,
    } as SendTextTouchResponse, { status: 200 });

  } catch (error) {
    console.error('Text touch API error:', error);
    return Response.json({
      success: false,
      campaignId: '',
      summary: {
        campaignId: '',
        boardId: '',
        sent: 0,
        failed: 0,
        pending: 0,
        results: [],
        executedAt: new Date().toISOString(),
        totalAttempted: 0,
      },
      error: error instanceof Error ? error.message : 'Unknown error',
    } as SendTextTouchResponse, { status: 500 });
  }
}
