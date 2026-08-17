import { useEffect, useState } from 'react';
import { Database, Server, CheckCircle, XCircle, AlertTriangle } from 'lucide-react';

interface HealthData {
  status: string;
  database: string;
  queue: string;
  browser: string;
  worker: string;
  ai_engine: string;
  last_event_at?: string;
}

export default function SystemHealth() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchHealth = async () => {
      try {
        const res = await fetch('/api/dashboard/health');
        setHealth(await res.json());
      } catch (err) {}
      setLoading(false);
    };

    fetchHealth();
    const interval = setInterval(fetchHealth, 5000);
    return () => clearInterval(interval);
  }, []);

  if (loading && !health) {
    return <div className="flex items-center justify-center h-64 tech-mono text-[var(--text-tertiary)] animate-pulse">Checking system diagnostics...</div>;
  }

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-medium tracking-tight">System Health</h2>
          <p className="text-[var(--text-secondary)] mt-1">Infrastructure diagnostics and component status</p>
        </div>
        
        {health?.worker === 'OFFLINE' ? (
          <button 
            onClick={async () => {
              try {
                await fetch('/api/dashboard/worker/start', { method: 'POST' });
                // Force a quick refresh of health status
                const res = await fetch('/api/dashboard/health');
                setHealth(await res.json());
              } catch (e) {}
            }}
            className="px-4 py-2 bg-[var(--color-brand-success)] text-white text-sm font-medium rounded-sm flex items-center gap-2 hover:opacity-90 transition-opacity"
          >
            <Server size={16} />
            Start Worker Process
          </button>
        ) : health?.worker === 'HEALTHY' ? (
          <button 
            onClick={async () => {
              try {
                await fetch('/api/dashboard/worker/stop', { method: 'POST' });
                const res = await fetch('/api/dashboard/health');
                setHealth(await res.json());
              } catch (e) {}
            }}
            className="px-4 py-2 bg-[var(--color-brand-error)] text-white text-sm font-medium rounded-sm flex items-center gap-2 hover:opacity-90 transition-opacity"
          >
            <XCircle size={16} />
            Stop Worker Process
          </button>
        ) : null}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-5 gap-6">
        <HealthCard title="Database" status={health?.database || 'UNKNOWN'} icon={Database} />
        <HealthCard title="Job Queue" status={health?.queue || 'UNKNOWN'} icon={Database} />
        <HealthCard title="Worker" status={health?.worker || 'UNKNOWN'} icon={Server} />
        <HealthCard title="Browser Pool" status={health?.browser || 'UNKNOWN'} icon={Server} />
        <HealthCard title="AI Engine" status={health?.ai_engine || 'UNKNOWN'} icon={Database} />
      </div>

      <div className="surface p-6 rounded-sm">
        <h3 className="font-medium text-sm uppercase tracking-widest text-[var(--text-secondary)] mb-4">Diagnostics Information</h3>
        <div className="space-y-4 text-sm text-[var(--text-primary)] tech-mono">
           <div className="flex justify-between border-b border-[var(--border-subtle)] pb-2">
             <span className="text-[var(--text-tertiary)]">Overall Status</span>
             <span className={health?.status === 'HEALTHY' ? 'text-[var(--color-brand-success)]' : 'text-[var(--color-brand-error)]'}>{health?.status || 'UNKNOWN'}</span>
           </div>
           <div className="flex justify-between border-b border-[var(--border-subtle)] pb-2">
             <span className="text-[var(--text-tertiary)]">Last System Event</span>
             <span>{health?.last_event_at ? health.last_event_at.replace('T', ' ') : 'N/A'}</span>
           </div>
           <div className="flex justify-between border-b border-[var(--border-subtle)] pb-2">
             <span className="text-[var(--text-tertiary)]">Dashboard UI Version</span>
             <span>2.0.0 (High Density)</span>
           </div>
        </div>
      </div>
    </div>
  );
}

function HealthCard({ title, status, icon: Icon }: any) {
  let StatusIcon = CheckCircle;
  let colorClass = "text-[var(--color-brand-success)]";
  let bgClass = "bg-[var(--color-brand-success-muted)]";
  
  if (status === 'UNHEALTHY' || status === 'ERROR' || status === 'OFFLINE') {
    StatusIcon = XCircle;
    colorClass = "text-[var(--color-brand-error)]";
    bgClass = "bg-[var(--color-brand-error-muted)]";
  } else if (status === 'UNKNOWN' || status === 'WARN') {
    StatusIcon = AlertTriangle;
    colorClass = "text-[var(--color-brand-warning)]";
    bgClass = "bg-[var(--color-brand-warning-muted)]";
  }

  return (
    <div className="surface p-5 rounded-sm flex flex-col gap-4">
      <div className="flex justify-between items-start">
        <div className={`p-2 rounded ${bgClass} ${colorClass}`}>
          <Icon size={20} />
        </div>
        <StatusIcon size={20} className={colorClass} />
      </div>
      <div>
        <h3 className="text-sm font-medium text-[var(--text-secondary)]">{title}</h3>
        <p className={`mt-1 font-bold tech-mono ${colorClass}`}>{status}</p>
      </div>
    </div>
  );
}
