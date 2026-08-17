import React from 'react';

type StatusType = 'SUCCESS' | 'ERROR' | 'WARNING' | 'INFO' | 'NEUTRAL';

interface StatusBadgeProps {
  status: string;
}

const mapStatusToType = (status: string): StatusType => {
  const upper = status.toUpperCase();
  if (upper.includes('SUCCESS') || upper.includes('COMPLETED') || upper.includes('APPROVED')) return 'SUCCESS';
  if (upper.includes('FAIL') || upper.includes('ERROR') || upper.includes('REJECTED')) return 'ERROR';
  if (upper.includes('WAITING') || upper.includes('REVIEW') || upper.includes('UNCERTAIN') || upper.includes('BLOCKED')) return 'WARNING';
  if (upper.includes('RUNNING') || upper.includes('PENDING')) return 'INFO';
  return 'NEUTRAL';
};

const getStatusIcon = (type: StatusType) => {
  switch (type) {
    case 'SUCCESS': return <span className="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>;
    case 'ERROR': return <span className="w-1.5 h-1.5 rounded-full bg-red-500"></span>;
    case 'WARNING': return <span className="w-1.5 h-1.5 rounded-full bg-amber-400"></span>;
    case 'INFO': return <span className="w-1.5 h-1.5 rounded-full bg-blue-400"></span>;
    default: return <span className="w-1.5 h-1.5 rounded-full bg-gray-500"></span>;
  }
};

export const StatusBadge: React.FC<StatusBadgeProps> = ({ status }) => {
  const type = mapStatusToType(status);
  const typeClass = `status-badge-${type.toLowerCase()}`;
  
  return (
    <span className={`status-badge ${typeClass}`}>
      {getStatusIcon(type)}
      {status.replace(/_/g, ' ')}
    </span>
  );
};
