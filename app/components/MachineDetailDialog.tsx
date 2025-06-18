import React, { useEffect, useState } from 'react';
import { Dialog } from '@headlessui/react';
import { XIcon } from '@heroicons/react/outline';
import { LineChart, PieChart } from './Charts';
import { formatDistanceToNow } from 'date-fns';

interface MachineDetailDialogProps {
  machine: any;
  onClose: () => void;
}

const MachineDetailDialog: React.FC<MachineDetailDialogProps> = ({ machine, onClose }) => {
  const [history, setHistory] = useState<any>(null);

  useEffect(() => {
    fetchMachineHistory();
  }, [machine.machine_id]);

  const fetchMachineHistory = async () => {
    try {
      const response = await fetch(
        `http://172.18.7.89:4470/production_monitoring/machine-history/${machine.machine_id}`
      );
      const data = await response.json();
      setHistory(data);
    } catch (error) {
      console.error('Error fetching machine history:', error);
    }
  };

  return (
    <Dialog
      as="div"
      className="fixed inset-0 z-10 overflow-y-auto"
      onClose={onClose}
      open={true}
    >
      <div className="min-h-screen px-4 text-center">
        <Dialog.Overlay className="fixed inset-0 bg-black opacity-30" />

        <div className="inline-block w-full max-w-4xl my-8 p-6 text-left align-middle transition-all transform bg-white shadow-xl rounded-2xl">
          {/* Header */}
          <div className="flex justify-between items-start">
            <Dialog.Title as="h3" className="text-2xl font-medium text-gray-900">
              {machine.machine_name}
            </Dialog.Title>
            <button
              onClick={onClose}
              className="text-gray-400 hover:text-gray-500"
            >
              <XIcon className="h-6 w-6" />
            </button>
          </div>

          {/* Content */}
          <div className="mt-6 space-y-6">
            {/* Current Status Section */}
            <section className="bg-gray-50 rounded-lg p-6">
              <h4 className="text-lg font-medium text-gray-900 mb-4">Current Status</h4>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <StatusItem
                  label="Status"
                  value={machine.status}
                  className={getStatusColor(machine.status)}
                />
                <StatusItem
                  label="Part Count"
                  value={machine.part_count}
                />
                <StatusItem
                  label="Active Program"
                  value={machine.active_program || 'N/A'}
                />
                <StatusItem
                  label="Last Updated"
                  value={formatDistanceToNow(new Date(machine.last_updated), { addSuffix: true })}
                />
              </div>
            </section>

            {/* Charts Section */}
            {history && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {/* Production Trend */}
                <section className="bg-white rounded-lg p-6 shadow">
                  <h4 className="text-lg font-medium text-gray-900 mb-4">Production Trend</h4>
                  <LineChart
                    data={history.hourly_production}
                    xKey="timestamp"
                    yKey="count"
                    label="Parts Produced"
                  />
                </section>

                {/* Status Distribution */}
                <section className="bg-white rounded-lg p-6 shadow">
                  <h4 className="text-lg font-medium text-gray-900 mb-4">Status Distribution</h4>
                  <PieChart
                    data={Object.entries(history.status_duration).map(([status, duration]) => ({
                      name: status,
                      value: Number(duration)
                    }))}
                  />
                </section>
              </div>
            )}

            {/* History Timeline */}
            <section className="bg-white rounded-lg p-6 shadow">
              <h4 className="text-lg font-medium text-gray-900 mb-4">Status History</h4>
              <div className="space-y-4">
                {history?.status_changes.map((change: any, index: number) => (
                  <TimelineItem
                    key={index}
                    timestamp={new Date(change.timestamp)}
                    status={change.status}
                    program={change.program}
                  />
                ))}
              </div>
            </section>
          </div>
        </div>
      </div>
    </Dialog>
  );
};

// Helper Components
const StatusItem: React.FC<{
  label: string;
  value: string | number;
  className?: string;
}> = ({ label, value, className = '' }) => (
  <div>
    <p className="text-sm text-gray-500">{label}</p>
    <p className={`text-sm font-medium mt-1 ${className}`}>{value}</p>
  </div>
);

const TimelineItem: React.FC<{
  timestamp: Date;
  status: string;
  program?: string;
}> = ({ timestamp, status, program }) => (
  <div className="flex items-start">
    <div className="flex-shrink-0 w-2 h-2 mt-2 rounded-full bg-blue-500" />
    <div className="ml-4">
      <p className="text-sm text-gray-500">
        {formatDistanceToNow(timestamp, { addSuffix: true })}
      </p>
      <p className="text-sm font-medium text-gray-900">{status}</p>
      {program && (
        <p className="text-sm text-gray-500">Program: {program}</p>
      )}
    </div>
  </div>
);

const getStatusColor = (status: string) => {
  const colors = {
    RUNNING: 'text-green-600',
    IDLE: 'text-yellow-600',
    BREAKDOWN: 'text-red-600',
    MAINTENANCE: 'text-blue-600',
  };
  return colors[status as keyof typeof colors] || 'text-gray-600';
};

export default MachineDetailDialog; 