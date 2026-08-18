import { useEffect, useState } from 'react';
import { Clock, Play, Square, Settings, RefreshCw, AlertTriangle, CheckCircle } from 'lucide-react';
import { apiFetch } from '../services/operations';

interface SchedulerStatus {
  phase: string;
  scheduler_enabled: boolean;
  scheduled_start_time: string;
  timezone: string;
  current_time_local: string;
  next_transition: string | null;
  last_transition_at: string | null;
  last_start_attempt: string | null;
  failed_services: string[];
  services: Record<string, any>;
}

export default function ProjectScheduler() {
  const [status, setStatus] = useState<SchedulerStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [editMode, setEditMode] = useState(false);
  const [editTime, setEditTime] = useState('');
  const [editTz, setEditTz] = useState('');
  const [actionLoading, setActionLoading] = useState(false);

  const fetchStatus = async () => {
    try {
      const data = await apiFetch('/api/scheduler/status');
      setStatus(data);
      if (!editMode) {
        setEditTime(data.scheduled_start_time);
        setEditTz(data.timezone);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 5000);
    return () => clearInterval(interval);
  }, [editMode]);

  const handleEnable = async () => {
    setActionLoading(true);
    try {
      await apiFetch('/api/scheduler/enable', {
        method: 'POST',
        body: JSON.stringify({ start_time: editTime, timezone: editTz }),
      });
      setEditMode(false);
      await fetchStatus();
    } catch (e) {
      alert('Failed to enable scheduler');
    }
    setActionLoading(false);
  };

  const handleDisable = async () => {
    setActionLoading(true);
    try {
      await apiFetch('/api/scheduler/disable', { method: 'POST' });
      await fetchStatus();
    } catch (e) {
      alert('Failed to disable scheduler');
    }
    setActionLoading(false);
  };

  const handleTick = async () => {
    setActionLoading(true);
    try {
      await apiFetch('/api/scheduler/tick', { method: 'POST' });
      await fetchStatus();
    } catch (e) {
      alert('Failed to trigger tick');
    }
    setActionLoading(false);
  };

  if (loading && !status) {
    return <div className="animate-pulse tech-mono text-[var(--text-tertiary)] flex items-center justify-center h-64">Loading scheduler state...</div>;
  }

  if (!status) return null;

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-medium tracking-tight">Project Scheduler</h2>
          <p className="text-[var(--text-secondary)] mt-1">Two-phase startup system state</p>
        </div>

        <div className="flex gap-2">
           <button
             onClick={handleTick}
             disabled={actionLoading}
             className="px-3 py-1.5 surface border border-[var(--border-subtle)] text-[13px] rounded hover:bg-white/5 transition-colors flex items-center gap-2"
           >
             <RefreshCw size={14} className={actionLoading ? "animate-spin" : ""} />
             Force Evaluation
           </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        
        {/* Configuration Panel */}
        <div className="surface p-6 rounded-sm col-span-1 border border-[var(--border-subtle)] flex flex-col">
          <div className="flex justify-between items-center mb-6">
            <h3 className="font-medium text-sm uppercase tracking-widest text-[var(--text-secondary)] flex items-center gap-2">
              <Settings size={16} />
              Configuration
            </h3>
            <span className={`px-2 py-0.5 text-[10px] uppercase font-bold rounded ${status.scheduler_enabled ? 'bg-[var(--color-brand-success-muted)] text-[var(--color-brand-success)]' : 'bg-white/10 text-white'}`}>
              {status.scheduler_enabled ? 'ENABLED' : 'DISABLED'}
            </span>
          </div>

          <div className="space-y-4 flex-1">
            <div>
              <label className="block text-[11px] text-[var(--text-tertiary)] uppercase tracking-widest mb-1.5">Start Time (HH:MM)</label>
              {editMode ? (
                <input 
                  type="time" 
                  value={editTime}
                  onChange={(e) => setEditTime(e.target.value)}
                  className="w-full bg-[var(--bg-app)] border border-[var(--border-subtle)] text-white px-3 py-2 rounded-sm text-sm focus:outline-none focus:border-blue-500 transition-colors"
                />
              ) : (
                <div className="text-xl font-mono text-[var(--text-primary)]">{status.scheduled_start_time}</div>
              )}
            </div>

            <div>
              <label className="block text-[11px] text-[var(--text-tertiary)] uppercase tracking-widest mb-1.5">Timezone</label>
              {editMode ? (
                <input 
                  type="text" 
                  value={editTz}
                  onChange={(e) => setEditTz(e.target.value)}
                  className="w-full bg-[var(--bg-app)] border border-[var(--border-subtle)] text-white px-3 py-2 rounded-sm text-sm focus:outline-none focus:border-blue-500 transition-colors"
                />
              ) : (
                <div className="text-sm tech-mono text-[var(--text-secondary)]">{status.timezone}</div>
              )}
            </div>
            
            <div className="pt-2 border-t border-[var(--border-subtle)]">
               <label className="block text-[11px] text-[var(--text-tertiary)] uppercase tracking-widest mb-1.5">Current Local Time</label>
               <div className="text-sm tech-mono text-blue-400">{status.current_time_local}</div>
            </div>
          </div>

          <div className="mt-6 pt-4 border-t border-[var(--border-subtle)] flex gap-2">
            {editMode ? (
              <>
                <button 
                  onClick={handleEnable} disabled={actionLoading}
                  className="flex-1 bg-[var(--color-brand-success)] text-white py-2 text-sm font-medium rounded-sm hover:opacity-90 transition-opacity"
                >
                  Save & Enable
                </button>
                <button 
                  onClick={() => setEditMode(false)} disabled={actionLoading}
                  className="flex-1 bg-white/10 text-white py-2 text-sm font-medium rounded-sm hover:bg-white/20 transition-colors"
                >
                  Cancel
                </button>
              </>
            ) : (
              <>
                {!status.scheduler_enabled ? (
                  <button 
                    onClick={() => setEditMode(true)}
                    className="flex-1 bg-[var(--color-brand-success)] text-white py-2 text-sm font-medium rounded-sm flex items-center justify-center gap-2 hover:opacity-90 transition-opacity"
                  >
                    <Play size={16} /> Enable Scheduler
                  </button>
                ) : (
                  <>
                    <button 
                      onClick={() => setEditMode(true)}
                      className="flex-1 bg-white/10 text-white py-2 text-sm font-medium rounded-sm hover:bg-white/20 transition-colors"
                    >
                      Edit Config
                    </button>
                    <button 
                      onClick={handleDisable} disabled={actionLoading}
                      className="flex-1 bg-[var(--color-brand-error)] text-white py-2 text-sm font-medium rounded-sm flex items-center justify-center gap-2 hover:opacity-90 transition-opacity"
                    >
                      <Square size={16} /> Disable
                    </button>
                  </>
                )}
              </>
            )}
          </div>
        </div>

        {/* State Machine Panel */}
        <div className="col-span-1 lg:col-span-2 space-y-6">
          <div className="surface p-6 rounded-sm border border-[var(--border-subtle)]">
            <h3 className="font-medium text-sm uppercase tracking-widest text-[var(--text-secondary)] mb-6 flex items-center gap-2">
              <Clock size={16} />
              Current State
            </h3>

            <div className="flex items-center gap-6 mb-8">
              <div className="w-1/3">
                <div className="text-[11px] text-[var(--text-tertiary)] uppercase tracking-widest mb-1">Phase</div>
                <div className={`text-2xl font-bold tracking-tight ${
                  status.phase === 'FULL_RUNNING' ? 'text-[var(--color-brand-success)]' : 
                  status.phase === 'UI_ONLY' ? 'text-blue-400' :
                  status.phase === 'DEGRADED' ? 'text-[var(--color-brand-error)]' :
                  'text-[var(--text-secondary)]'
                }`}>
                  {status.phase}
                </div>
              </div>
              
              <div className="w-2/3 border-l border-[var(--border-subtle)] pl-6">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <div className="text-[11px] text-[var(--text-tertiary)] uppercase tracking-widest mb-1">Next Transition</div>
                    <div className="text-sm tech-mono text-[var(--text-primary)]">
                      {status.next_transition ? status.next_transition.split('T')[1].substring(0,5) : 'None pending'}
                    </div>
                  </div>
                  <div>
                    <div className="text-[11px] text-[var(--text-tertiary)] uppercase tracking-widest mb-1">Last Change</div>
                    <div className="text-sm tech-mono text-[var(--text-primary)]">
                      {status.last_transition_at ? status.last_transition_at.replace('T', ' ').substring(0, 19) : 'Never'}
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {status.failed_services.length > 0 && (
              <div className="bg-[var(--color-brand-error-muted)] border border-[var(--color-brand-error)]/30 p-4 rounded-sm mb-6 flex items-start gap-3">
                <AlertTriangle size={18} className="text-[var(--color-brand-error)] shrink-0 mt-0.5" />
                <div>
                  <h4 className="text-sm font-medium text-[var(--color-brand-error)]">Failed Services Detected</h4>
                  <p className="text-xs text-[var(--color-brand-error)]/80 mt-1 tech-mono">
                    {status.failed_services.join(', ')}
                  </p>
                </div>
              </div>
            )}
          </div>

          <div className="surface p-6 rounded-sm border border-[var(--border-subtle)]">
             <h3 className="font-medium text-sm uppercase tracking-widest text-[var(--text-secondary)] mb-6">Service Health</h3>
             <div className="space-y-3">
               {Object.values(status.services).map((svc: any) => (
                 <div key={svc.name} className="flex items-center justify-between p-3 bg-white/5 rounded-sm border border-white/5">
                   <div className="flex items-center gap-3">
                     {svc.status === 'RUNNING' ? (
                       <CheckCircle size={16} className="text-[var(--color-brand-success)]" />
                     ) : svc.status === 'FAILED' ? (
                       <AlertTriangle size={16} className="text-[var(--color-brand-error)]" />
                     ) : (
                       <Square size={16} className="text-[var(--text-tertiary)]" />
                     )}
                     <div>
                       <div className="font-medium text-sm">{svc.name}</div>
                       {svc.pid && <div className="text-[10px] text-[var(--text-tertiary)] tech-mono">PID: {svc.pid}</div>}
                     </div>
                   </div>
                   <div className={`text-xs font-bold uppercase tracking-widest ${
                     svc.status === 'RUNNING' ? 'text-[var(--color-brand-success)]' : 
                     svc.status === 'FAILED' ? 'text-[var(--color-brand-error)]' : 
                     'text-[var(--text-tertiary)]'
                   }`}>
                     {svc.status}
                   </div>
                 </div>
               ))}
               
               {Object.keys(status.services).length === 0 && (
                 <div className="text-sm text-[var(--text-tertiary)] text-center py-4">No services registered or state uninitialized.</div>
               )}
             </div>
          </div>
        </div>

      </div>
    </div>
  );
}
