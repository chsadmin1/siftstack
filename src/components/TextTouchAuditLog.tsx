/**
 * TextTouchAuditLog Component
 *
 * Displays recent text touch campaign audit records for compliance tracking.
 * Shows sender, recipient, message, timestamp, and delivery status.
 */

import React, { useState, useEffect } from 'react';
import { TextTouchAuditRecord } from '../db/schema';

interface TextTouchAuditLogProps {
  campaignId?: string;
  limit?: number;
}

export function TextTouchAuditLog({
  campaignId,
  limit = 10,
}: TextTouchAuditLogProps) {
  const [records, setRecords] = useState<TextTouchAuditRecord[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch audit records (mock implementation)
  const fetchAuditRecords = async () => {
    setIsLoading(true);
    setError(null);

    try {
      // TODO: Implement actual audit fetch from API
      // const response = await fetch(`/api/touches/text/audit?campaign=${campaignId || ''}&limit=${limit}`);
      // if (!response.ok) throw new Error('Failed to fetch audit records');
      // const data = await response.json();
      // setRecords(data);

      // Mock data for demo
      setRecords([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load audit records');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchAuditRecords();
  }, [campaignId]);

  if (isLoading) {
    return (
      <div className="p-4 bg-gray-50 rounded-md">
        <p className="text-sm text-gray-600">Loading audit records...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 bg-red-50 rounded-md border border-red-200">
        <p className="text-sm text-red-700">Error: {error}</p>
      </div>
    );
  }

  if (records.length === 0) {
    return (
      <div className="p-4 bg-gray-50 rounded-md">
        <p className="text-sm text-gray-600">No audit records available</p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm text-gray-700">
        <thead className="bg-gray-100 border-b">
          <tr>
            <th className="px-4 py-2 text-left font-semibold">Campaign ID</th>
            <th className="px-4 py-2 text-left font-semibold">Lead ID</th>
            <th className="px-4 py-2 text-left font-semibold">Phone</th>
            <th className="px-4 py-2 text-left font-semibold">Status</th>
            <th className="px-4 py-2 text-left font-semibold">Sent At</th>
            <th className="px-4 py-2 text-left font-semibold">Message</th>
          </tr>
        </thead>
        <tbody>
          {records.map((record) => (
            <tr key={record.id} className="border-b hover:bg-gray-50">
              <td className="px-4 py-2 font-mono text-xs">{record.campaignId}</td>
              <td className="px-4 py-2 font-mono text-xs">{record.leadId}</td>
              <td className="px-4 py-2">{record.phoneNumber}</td>
              <td className="px-4 py-2">
                <span
                  className={`px-2 py-1 rounded text-xs font-semibold ${
                    record.status === 'sent'
                      ? 'bg-green-100 text-green-800'
                      : record.status === 'failed'
                      ? 'bg-red-100 text-red-800'
                      : 'bg-yellow-100 text-yellow-800'
                  }`}
                >
                  {record.status}
                </span>
              </td>
              <td className="px-4 py-2 text-xs">
                {new Date(record.sentAt).toLocaleString()}
              </td>
              <td className="px-4 py-2 max-w-xs truncate text-xs">
                {record.messageSent || '-'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default TextTouchAuditLog;
