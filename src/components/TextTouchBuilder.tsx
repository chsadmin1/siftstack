/**
 * TextTouchBuilder Component
 *
 * Single-page component for building and executing text message campaigns.
 * Features:
 * - SIFT board selector (dropdown)
 * - Message template composer with live preview
 * - Send button (disabled until form valid)
 * - Error/success toast notifications
 * - Results modal on successful send
 */

import React, { useState } from 'react';
import { useTextTouch } from '../hooks/useTextTouch';
import { TextTouchExecutionSummary } from '../types/textTouch';

interface Toast {
  type: 'error' | 'success' | 'info';
  message: string;
  id: string;
}

export function TextTouchBuilder() {
  const hook = useTextTouch();
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [showConfirm, setShowConfirm] = useState(false);

  // Display error as toast
  React.useEffect(() => {
    if (hook.error) {
      const id = Date.now().toString();
      setToasts(prev => [...prev, {
        type: 'error',
        message: hook.error,
        id,
      }]);
      // Auto-remove after 5 seconds
      setTimeout(() => {
        setToasts(prev => prev.filter(t => t.id !== id));
      }, 5000);
    }
  }, [hook.error]);

  const handleSendClick = () => {
    // Show confirmation modal
    setShowConfirm(true);
  };

  const handleConfirmSend = async () => {
    setShowConfirm(false);
    await hook.sendCampaign();
  };

  const selectedBoard = hook.boards.find(b => b.id === hook.selectedBoardId);
  const leadCountText = selectedBoard ? ` (${selectedBoard.leadCount} leads)` : '';

  return (
    <div className="text-touch-builder">
      <div className="container max-w-2xl mx-auto p-6">
        <h1 className="text-3xl font-bold mb-2">Text Touch Campaign Builder</h1>
        <p className="text-gray-600 mb-6">
          Select a SIFT board, compose your message, and send SMS to targeted leads.
        </p>

        <div className="card bg-white rounded-lg shadow-md p-6">
          {/* Section 1: Board Selection */}
          <fieldset className="mb-6">
            <label htmlFor="board-select" className="block text-sm font-semibold mb-2">
              Target SIFT Board
            </label>
            <select
              id="board-select"
              value={hook.selectedBoardId || ''}
              onChange={(e) => hook.selectBoard(e.target.value)}
              disabled={hook.isLoading}
              className="w-full px-4 py-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:bg-gray-100"
            >
              <option value="">
                {hook.isLoading ? 'Loading boards...' : 'Select a board'}
              </option>
              {hook.boards.map(board => (
                <option key={board.id} value={board.id}>
                  {board.name} ({board.leadCount} leads)
                </option>
              ))}
            </select>
            {hook.isLoading && (
              <p className="text-sm text-gray-500 mt-1">Loading available boards...</p>
            )}
          </fieldset>

          {/* Section 2: Message Composer */}
          <fieldset className="mb-6">
            <label htmlFor="message-input" className="block text-sm font-semibold mb-2">
              Message Template
            </label>
            <textarea
              id="message-input"
              value={hook.messageTemplate}
              onChange={(e) => hook.updateMessage(e.target.value)}
              placeholder="Compose your message. Use {firstName}, {lastName}, {phoneNumber}, {propertyAddress}, {propertyPrice} for dynamic fields."
              className="w-full px-4 py-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-vertical"
              rows={4}
              disabled={hook.isSending}
            />
            <p className="text-xs text-gray-500 mt-2">
              Character count: {hook.messageTemplate.length}
            </p>
          </fieldset>

          {/* Section 3: Live Preview */}
          {hook.messageTemplate && (
            <fieldset className="mb-6 p-4 bg-blue-50 rounded-md border border-blue-200">
              <label className="block text-sm font-semibold mb-2 text-blue-900">
                Preview
              </label>
              <p className="text-sm text-blue-800 whitespace-pre-wrap">
                {hook.preview}
              </p>
            </fieldset>
          )}

          {/* Section 4: Send Button & Actions */}
          <div className="flex gap-3 mb-6">
            <button
              onClick={handleSendClick}
              disabled={!hook.canSend()}
              className="flex-1 px-6 py-2 bg-blue-600 text-white font-semibold rounded-md hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed flex items-center justify-center gap-2"
            >
              {hook.isSending ? (
                <>
                  <span className="inline-block animate-spin">⏳</span>
                  Sending...
                </>
              ) : (
                'Send Campaign'
              )}
            </button>
            <button
              onClick={hook.clearCampaign}
              disabled={hook.isSending}
              className="px-6 py-2 bg-gray-300 text-gray-700 font-semibold rounded-md hover:bg-gray-400 disabled:bg-gray-200 disabled:cursor-not-allowed"
            >
              Clear
            </button>
          </div>
        </div>

        {/* Confirmation Modal */}
        {showConfirm && (
          <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
            <div className="bg-white rounded-lg p-6 max-w-md">
              <h2 className="text-xl font-bold mb-2">Confirm Send</h2>
              <p className="text-gray-600 mb-4">
                Send this message to {selectedBoard?.leadCount || '?'} leads from{' '}
                <strong>{selectedBoard?.name || 'selected board'}</strong>?
              </p>
              <p className="text-sm text-gray-500 mb-4 p-3 bg-gray-50 rounded">
                {hook.preview}
              </p>
              <div className="flex gap-3 justify-end">
                <button
                  onClick={() => setShowConfirm(false)}
                  className="px-4 py-2 text-gray-700 border border-gray-300 rounded-md hover:bg-gray-50"
                >
                  Cancel
                </button>
                <button
                  onClick={handleConfirmSend}
                  className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700"
                >
                  Confirm Send
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Results Modal */}
        {hook.success && (
          <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
            <div className="bg-white rounded-lg p-6 max-w-lg">
              <h2 className="text-2xl font-bold mb-4 text-green-600">✓ Campaign Sent</h2>
              <div className="space-y-3 mb-6">
                <div className="grid grid-cols-3 gap-4">
                  <div className="text-center p-3 bg-green-50 rounded">
                    <div className="text-2xl font-bold text-green-600">{hook.success.sent}</div>
                    <div className="text-sm text-gray-600">Sent</div>
                  </div>
                  <div className="text-center p-3 bg-red-50 rounded">
                    <div className="text-2xl font-bold text-red-600">{hook.success.failed}</div>
                    <div className="text-sm text-gray-600">Failed</div>
                  </div>
                  <div className="text-center p-3 bg-yellow-50 rounded">
                    <div className="text-2xl font-bold text-yellow-600">{hook.success.pending}</div>
                    <div className="text-sm text-gray-600">Pending</div>
                  </div>
                </div>
                <p className="text-sm text-gray-600">
                  Executed at: {new Date(hook.success.executedAt).toLocaleString()}
                </p>
              </div>
              <button
                onClick={() => {
                  hook.clearSuccess();
                  hook.clearCampaign();
                }}
                className="w-full px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700"
              >
                Done
              </button>
            </div>
          </div>
        )}

        {/* Toast Notifications */}
        <div className="fixed bottom-4 right-4 space-y-2 z-40">
          {toasts.map(toast => (
            <div
              key={toast.id}
              className={`px-4 py-3 rounded-md text-white font-medium ${
                toast.type === 'error' ? 'bg-red-500' :
                toast.type === 'success' ? 'bg-green-500' :
                'bg-blue-500'
              }`}
            >
              {toast.message}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default TextTouchBuilder;
