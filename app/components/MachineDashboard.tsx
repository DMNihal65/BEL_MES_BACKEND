import React, { useEffect, useState } from 'react';
import { useWebSocket } from '../hooks/useWebSocket';
import MachineDetailDialog from './MachineDetailDialog';
import { PieChart, LineChart } from './Charts';
import { StatusBadge, ProductionMetrics } from './MachineComponents';

interface MachineData {
  machine_id: number;
  machine_name: string;
  status: string;
  program_number: string | null;
  active_program: string | null;
  selected_program: string | null;
  part_count: number;
  job_status: number | null;
  last_updated: string;
  job_in_progress: number | null;
}

const MachineDashboard: React.FC = () => {
  const [selectedMachine, setSelectedMachine] = useState<MachineData | null>(null);
  const [machines, setMachines] = useState<MachineData[]>([]);
  const wsUrl = 'ws://172.18.7.89:4470/production_monitoring/ws/live-status/';
  
  const { lastMessage } = useWebSocket(wsUrl);

  useEffect(() => {
    if (lastMessage) {
      try {
        const data = JSON.parse(lastMessage);
        if (data.machines) {
          setMachines(data.machines);
        }
      } catch (error) {
        console.error('Error parsing WebSocket data:', error);
      }
    }
  }, [lastMessage]);

  return (
    <div className="min-h-screen bg-gray-100">
      {/* Header */}
      <div className="bg-white shadow">
        <div className="max-w-7xl mx-auto py-4 px-4 sm:px-6 lg:px-8">
          <h1 className="text-2xl font-semibold text-gray-900">Machine Dashboard</h1>
        </div>
      </div>

      {/* Dashboard Content */}
      <div className="max-w-7xl mx-auto py-6 px-4 sm:px-6 lg:px-8">
        {/* Summary Cards */}
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4 mb-6">
          <SummaryCard
            title="Total Machines"
            value={machines.length}
            icon={<MachineIcon />}
          />
          <SummaryCard
            title="Running"
            value={machines.filter(m => m.status === 'RUNNING').length}
            icon={<RunningIcon />}
            className="bg-green-50"
          />
          <SummaryCard
            title="Idle"
            value={machines.filter(m => m.status === 'IDLE').length}
            icon={<IdleIcon />}
            className="bg-yellow-50"
          />
          <SummaryCard
            title="Down"
            value={machines.filter(m => ['BREAKDOWN', 'MAINTENANCE'].includes(m.status)).length}
            icon={<DownIcon />}
            className="bg-red-50"
          />
        </div>

        {/* Machine Grid */}
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {machines.map((machine) => (
            <MachineCard
              key={machine.machine_id}
              machine={machine}
              onClick={() => setSelectedMachine(machine)}
            />
          ))}
        </div>
      </div>

      {/* Machine Detail Dialog */}
      {selectedMachine && (
        <MachineDetailDialog
          machine={selectedMachine}
          onClose={() => setSelectedMachine(null)}
        />
      )}
    </div>
  );
};

// Subcomponents
const SummaryCard: React.FC<{
  title: string;
  value: number;
  icon: React.ReactNode;
  className?: string;
}> = ({ title, value, icon, className = '' }) => (
  <div className={`p-6 bg-white rounded-lg shadow ${className}`}>
    <div className="flex items-center">
      <div className="flex-shrink-0">{icon}</div>
      <div className="ml-4">
        <h3 className="text-lg font-medium text-gray-900">{title}</h3>
        <p className="text-2xl font-semibold text-gray-700">{value}</p>
      </div>
    </div>
  </div>
);

const MachineCard: React.FC<{
  machine: MachineData;
  onClick: () => void;
}> = ({ machine, onClick }) => {
  const statusColors = {
    RUNNING: 'bg-green-100 text-green-800',
    IDLE: 'bg-yellow-100 text-yellow-800',
    BREAKDOWN: 'bg-red-100 text-red-800',
    MAINTENANCE: 'bg-blue-100 text-blue-800',
  };

  return (
    <div
      onClick={onClick}
      className="bg-white rounded-lg shadow hover:shadow-lg transition-shadow duration-200 cursor-pointer"
    >
      <div className="p-6">
        <div className="flex justify-between items-start">
          <div>
            <h3 className="text-lg font-medium text-gray-900">{machine.machine_name}</h3>
            <span className={`inline-flex items-center px-3 py-1 rounded-full text-sm font-medium mt-2 ${
              statusColors[machine.status as keyof typeof statusColors] || 'bg-gray-100 text-gray-800'
            }`}>
              {machine.status}
            </span>
          </div>
          <ProductionMetrics
            partCount={machine.part_count}
            lastUpdated={new Date(machine.last_updated)}
          />
        </div>
        
        <div className="mt-4 grid grid-cols-2 gap-4">
          <div>
            <p className="text-sm text-gray-500">Program</p>
            <p className="text-sm font-medium text-gray-900">{machine.active_program || 'N/A'}</p>
          </div>
          <div>
            <p className="text-sm text-gray-500">Job Status</p>
            <p className="text-sm font-medium text-gray-900">
              {machine.job_in_progress ? 'In Progress' : 'No Job'}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default MachineDashboard; 