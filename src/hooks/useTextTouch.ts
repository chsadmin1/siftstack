/**
 * React Hook: useTextTouch
 *
 * Manages state and logic for text touch campaign builder.
 * Provides board selection, message templating, and send functionality.
 */

import { useState, useCallback, useEffect } from 'react';
import { SiftBoard, TextTouchCampaign, TextTouchExecutionSummary } from '../types/textTouch';

interface UseTextTouchState {
  selectedBoardId: string | null;
  boards: SiftBoard[];
  messageTemplate: string;
  preview: string;
  isLoading: boolean;
  isSending: boolean;
  error: string | null;
  success: TextTouchExecutionSummary | null;
}

export function useTextTouch() {
  const [state, setState] = useState<UseTextTouchState>({
    selectedBoardId: null,
    boards: [],
    messageTemplate: '',
    preview: '',
    isLoading: true,
    isSending: false,
    error: null,
    success: null,
  });

  /**
   * Fetch available SIFT boards from /api/sift/boards
   */
  const fetchBoards = useCallback(async () => {
    setState(prev => ({ ...prev, isLoading: true, error: null }));
    try {
      const response = await fetch('/api/sift/boards');
      if (!response.ok) {
        throw new Error(`Failed to fetch boards: ${response.statusText}`);
      }
      const boards = await response.json();
      setState(prev => ({
        ...prev,
        boards: boards || [],
        isLoading: false,
      }));
    } catch (error) {
      setState(prev => ({
        ...prev,
        error: error instanceof Error ? error.message : 'Unknown error fetching boards',
        isLoading: false,
      }));
    }
  }, []);

  /**
   * Load boards on component mount
   */
  useEffect(() => {
    fetchBoards();
  }, [fetchBoards]);

  /**
   * Select a SIFT board
   */
  const selectBoard = useCallback((boardId: string) => {
    setState(prev => ({
      ...prev,
      selectedBoardId: boardId,
      error: null,
    }));
  }, []);

  /**
   * Update message template and generate preview
   */
  const updateMessage = useCallback((template: string) => {
    const preview = substitutePreview(template);
    setState(prev => ({
      ...prev,
      messageTemplate: template,
      preview,
    }));
  }, []);

  /**
   * Generate preview text by substituting template placeholders
   * Mock substitution: {firstName} -> "John", {lastName} -> "Doe"
   */
  function substitutePreview(template: string): string {
    return template
      .replace(/{firstName}/g, 'John')
      .replace(/{lastName}/g, 'Doe')
      .replace(/{phoneNumber}/g, '(555) 123-4567')
      .replace(/{propertyAddress}/g, '123 Main St, Anytown, ST 12345')
      .replace(/{propertyPrice}/g, '$250,000');
  }

  /**
   * Validate campaign before sending
   */
  const validateCampaign = useCallback((): string | null => {
    if (!state.selectedBoardId) {
      return 'Please select a SIFT board';
    }
    if (!state.messageTemplate.trim()) {
      return 'Please enter a message';
    }
    if (state.messageTemplate.trim().length < 10) {
      return 'Message must be at least 10 characters';
    }
    return null;
  }, [state.selectedBoardId, state.messageTemplate]);

  /**
   * Send text touch campaign to API
   */
  const sendCampaign = useCallback(async () => {
    const validationError = validateCampaign();
    if (validationError) {
      setState(prev => ({
        ...prev,
        error: validationError,
      }));
      return;
    }

    setState(prev => ({
      ...prev,
      isSending: true,
      error: null,
      success: null,
    }));

    try {
      const response = await fetch('/api/touches/text/send', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          boardId: state.selectedBoardId,
          messageTemplate: state.messageTemplate,
          sendAt: 'immediate',
        }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.error || `API error: ${response.statusText}`);
      }

      const result = await response.json();
      setState(prev => ({
        ...prev,
        isSending: false,
        success: result.summary,
        messageTemplate: '', // Clear form
      }));
    } catch (error) {
      setState(prev => ({
        ...prev,
        isSending: false,
        error: error instanceof Error ? error.message : 'Unknown error sending campaign',
      }));
    }
  }, [state.selectedBoardId, state.messageTemplate, validateCampaign]);

  /**
   * Clear the current campaign (reset form)
   */
  const clearCampaign = useCallback(() => {
    setState(prev => ({
      ...prev,
      selectedBoardId: null,
      messageTemplate: '',
      preview: '',
      error: null,
      success: null,
    }));
  }, []);

  /**
   * Clear the success message
   */
  const clearSuccess = useCallback(() => {
    setState(prev => ({
      ...prev,
      success: null,
    }));
  }, []);

  /**
   * Check if send button should be enabled
   */
  const canSend = useCallback((): boolean => {
    return !!(
      state.selectedBoardId &&
      state.messageTemplate.trim().length >= 10 &&
      !state.isSending &&
      !state.isLoading
    );
  }, [state.selectedBoardId, state.messageTemplate, state.isSending, state.isLoading]);

  return {
    // State
    selectedBoardId: state.selectedBoardId,
    boards: state.boards,
    messageTemplate: state.messageTemplate,
    preview: state.preview,
    isLoading: state.isLoading,
    isSending: state.isSending,
    error: state.error,
    success: state.success,

    // Actions
    selectBoard,
    updateMessage,
    sendCampaign,
    clearCampaign,
    clearSuccess,
    validateCampaign,
    canSend,

    // Refresh
    fetchBoards,
  };
}
