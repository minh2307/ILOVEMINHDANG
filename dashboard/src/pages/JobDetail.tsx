import { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { getOperations, previewOperation, executeOperation } from '../services/operations';
import type { OperationPolicy, OperationPreview } from '../services/operations';
import { StatusBadge } from '../components/ui/StatusBadge';
import { ArrowLeft, Clock, ExternalLink, ShieldAlert, XCircle, Database } from 'lucide-react';

interface JobEvent {
  timestamp: string;
  event_type: string;
  level: string;
  attempt: number;
}

export default function JobDetail() {
  const { id: jobId } = useParams();
  const [job, setJob] = useState<any>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  
  const [operations, setOperations] = useState<OperationPolicy[]>([]);
  const [preview, setPreview] = useState<OperationPreview | null>(null);
  const [reason, setReason] = useState("");
  const [executing, setExecuting] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  const loadData = () => {
    fetch(`/api/dashboard/jobs/${jobId}`)
      .then(res => res.json())
      .then(data => setJob(data));
      
    fetch(`/api/dashboard/jobs/${jobId}/events`)
      .then(res => res.json())
      .then(data => setEvents(data));
      
    if (jobId) {
      getOperations(jobId).then(ops => setOperations(ops)).catch(e => console.error(e));
    }
  };

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 5000);
    return () => clearInterval(interval);
  }, [jobId]);

  if (!job) {
    return <div className="flex items-center justify-center h-64 tech-mono text-[var(--text-tertiary)] animate-pulse">Loading job context...</div>;
  }

  const handleActionClick = async (op: string) => {
    try {
      setErrorMsg("");
      const result = await previewOperation(jobId as string, op);
      setPreview(result);
    } catch (e: any) {
      setErrorMsg(e.message);
    }
  };

  const confirmAction = async () => {
    if (!preview || !jobId) return;
    setExecuting(true);
    try {
      await executeOperation(jobId, preview.operation, reason);
      setPreview(null);
      setReason("");
      loadData();
    } catch (e: any) {
      setErrorMsg(e.message);
    }
    setExecuting(false);
  };

  const getRiskColor = (risk: string, allowed: boolean) => {
    if (!allowed) return "bg-[var(--bg-elevated)] text-[var(--text-tertiary)] border border-[var(--border-strong)] cursor-not-allowed opacity-60";
    if (risk === "HIGH_RISK") return "bg-[var(--color-brand-error)] text-white hover:opacity-90";
    if (risk === "MEDIUM_RISK") return "bg-[var(--color-brand-warning)] text-black hover:opacity-90";
    return "bg-[var(--color-brand-info)] text-white hover:opacity-90";
  };

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      
      {/* ACTION MODAL */}
      {preview && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="surface shadow-2xl rounded-sm max-w-lg w-full border border-[var(--border-strong)] overflow-hidden">
            <div className="p-5 border-b border-[var(--border-subtle)] bg-[var(--bg-app)]">
              <h2 className="text-lg font-medium">Confirm Operation: <span className="tech-mono text-[var(--color-brand-info)]">{preview.operation}</span></h2>
            </div>
            <div className="p-5 space-y-4">
              <div className="bg-[var(--bg-app)] p-3 rounded-sm border border-[var(--border-subtle)] space-y-2 text-sm text-[var(--text-secondary)]">
                <div className="flex justify-between"><span className="text-[var(--text-tertiary)]">Target Job</span><span className="tech-mono text-[var(--text-primary)]">{jobId}</span></div>
                <div className="flex justify-between"><span className="text-[var(--text-tertiary)]">Current Stage</span><span>{job.stage}</span></div>
                <div className="flex justify-between">
                  <span className="text-[var(--text-tertiary)]">Safety Risk</span>
                  <span className={`font-bold ${preview.risk.includes('HIGH') ? 'text-[var(--color-brand-error)]' : 'text-[var(--color-brand-warning)]'}`}>
                    {preview.risk.replace('_', ' ')}
                  </span>
                </div>
              </div>
              
              {preview.reason && (
                <div className="p-3 bg-[var(--color-brand-warning-muted)] border border-[var(--color-brand-warning-muted)] text-[var(--color-brand-warning)] text-sm rounded-sm flex gap-2 items-start">
                  <ShieldAlert size={16} className="shrink-0 mt-0.5" />
                  <p>{preview.reason}</p>
                </div>
              )}
              
              {errorMsg && (
                <div className="p-3 bg-[var(--color-brand-error-muted)] border border-[var(--color-brand-error-muted)] text-[var(--color-brand-error)] text-sm rounded-sm flex gap-2 items-start">
                  <XCircle size={16} className="shrink-0 mt-0.5" />
                  <p>{errorMsg}</p>
                </div>
              )}
              
              <div>
                <label className="block text-xs uppercase tracking-wider text-[var(--text-tertiary)] font-bold mb-2">Audit Reason (Required)</label>
                <input 
                  type="text" 
                  value={reason}
                  onChange={e => setReason(e.target.value)}
                  className="w-full bg-[var(--bg-app)] border border-[var(--border-strong)] rounded-sm px-3 py-2 text-sm text-[var(--text-primary)] focus:outline-none focus:border-[var(--color-brand-info)]" 
                  placeholder="e.g. Transient network failure, manually verified..."
                  autoFocus
                />
              </div>
            </div>
            <div className="p-4 border-t border-[var(--border-subtle)] bg-[var(--bg-app)] flex justify-end gap-3">
              <button 
                onClick={() => setPreview(null)}
                className="px-4 py-2 rounded-sm text-sm font-medium hover:bg-[var(--bg-elevated)] transition-colors text-[var(--text-secondary)]"
              >
                Cancel
              </button>
              <button 
                disabled={!reason.trim() || executing}
                onClick={confirmAction}
                className={`px-4 py-2 rounded-sm text-sm font-medium transition-colors disabled:opacity-50 ${preview.risk.includes('HIGH') ? 'bg-[var(--color-brand-error)] text-white' : 'bg-[var(--color-brand-info)] text-white'}`}
              >
                {executing ? "Executing..." : "Confirm Operation"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Breadcrumb */}
      <div>
        <Link to="/jobs" className="inline-flex items-center gap-2 text-sm text-[var(--text-tertiary)] hover:text-[var(--text-primary)] transition-colors">
          <ArrowLeft size={14} />
          Back to Jobs
        </Link>
      </div>

      {/* Header Area */}
      <div className="surface p-6 rounded-sm border-l-4 border-l-[var(--color-brand-info)]">
        <div className="flex flex-col md:flex-row md:items-start justify-between gap-6">
          <div className="space-y-2">
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-light tech-mono tracking-tight">{job.short_id}</h1>
              <StatusBadge status={job.status} />
            </div>
            <div className="text-sm text-[var(--text-secondary)] flex items-center gap-2">
              <span className="uppercase tracking-widest text-[10px] font-bold text-[var(--text-tertiary)]">Stage</span>
              {job.stage}
            </div>
            <div className="flex flex-wrap gap-4 mt-4 text-sm text-[var(--text-secondary)]">
              {job.source_url && (
                <a href={job.source_url} target="_blank" rel="noreferrer" className="flex items-center gap-1.5 hover:text-[var(--color-brand-info)] transition-colors">
                  <ExternalLink size={14} /> Source
                </a>
              )}
              {job.view_id && (
                <a href={job.view_id} target="_blank" rel="noreferrer" className="flex items-center gap-1.5 hover:text-[var(--color-brand-info)] transition-colors">
                  <ExternalLink size={14} /> CDHA
                </a>
              )}
              {job.permalink && (
                <a href={job.permalink} target="_blank" rel="noreferrer" className="flex items-center gap-1.5 hover:text-[var(--color-brand-info)] transition-colors">
                  <ExternalLink size={14} /> Facebook
                </a>
              )}
            </div>
          </div>
          
          {/* Operations Panel */}
          <div className="bg-[var(--bg-app)] p-3 rounded-sm border border-[var(--border-subtle)] shrink-0 min-w-[300px]">
            <h3 className="text-[10px] font-bold uppercase tracking-widest text-[var(--text-tertiary)] mb-3">Safe Operations</h3>
            <div className="grid grid-cols-2 gap-2">
              {operations.map(op => (
                <button
                  key={op.operation}
                  disabled={!op.allowed}
                  title={op.reason || ''}
                  onClick={() => handleActionClick(op.operation)}
                  className={`px-3 py-1.5 rounded-sm text-xs font-medium transition-all
                    ${getRiskColor(op.risk, op.allowed)}
                  `}
                >
                  {op.operation}
                </button>
              ))}
            </div>
            {operations.some(op => !op.allowed) && (
               <div className="mt-3 text-[10px] text-[var(--text-tertiary)] italic">Hover disabled buttons for safety reason.</div>
            )}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Timeline Log */}
        <div className="lg:col-span-2 surface rounded-sm flex flex-col h-[600px]">
          <div className="p-4 border-b border-[var(--border-subtle)] flex items-center gap-2">
            <Clock size={16} className="text-[var(--text-tertiary)]" />
            <h3 className="font-semibold text-sm uppercase tracking-widest text-[var(--text-secondary)]">Execution Timeline</h3>
          </div>
          <div className="flex-1 overflow-y-auto p-4 space-y-0 relative">
            <div className="absolute top-4 bottom-4 left-[27px] w-px bg-[var(--border-strong)] z-0"></div>
            {events.map((e, i) => {
               const isError = e.level.includes('ERROR') || e.level.includes('FAIL');
               const dotColor = isError ? 'bg-[var(--color-brand-error)]' : 'bg-[var(--text-secondary)]';
               
               return (
                <div key={i} className="relative z-10 flex gap-4 py-3 group">
                  <div className="mt-1 flex shrink-0 justify-center w-6">
                    <div className={`w-2 h-2 rounded-full ring-4 ring-[var(--bg-surface)] ${dotColor} group-hover:scale-125 transition-transform`}></div>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between mb-1">
                      <span className="tech-mono text-xs text-[var(--text-tertiary)]">
                        {e.timestamp.replace('T', ' ').substring(0, 19)}
                      </span>
                      <span className="text-[10px] uppercase font-bold text-[var(--text-tertiary)] bg-[var(--bg-app)] px-1.5 py-0.5 rounded-sm border border-[var(--border-subtle)]">
                        Attempt {e.attempt}
                      </span>
                    </div>
                    <div className={`font-semibold text-sm mb-0.5 ${isError ? 'text-[var(--color-brand-error)]' : 'text-[var(--text-primary)]'}`}>
                      {e.event_type}
                    </div>
                    {e.level !== e.event_type && (
                      <div className="text-xs text-[var(--text-secondary)] tech-mono">
                        → {e.level}
                      </div>
                    )}
                  </div>
                </div>
               );
            })}
          </div>
        </div>

        {/* State Information */}
        <div className="space-y-6">
          <div className="surface rounded-sm">
            <div className="p-4 border-b border-[var(--border-subtle)] flex items-center gap-2">
              <Database size={16} className="text-[var(--text-tertiary)]" />
              <h3 className="font-semibold text-sm uppercase tracking-widest text-[var(--text-secondary)]">Queue & Lease</h3>
            </div>
            <div className="p-4 space-y-4">
              <div className="flex justify-between items-center text-sm">
                <span className="text-[var(--text-tertiary)]">Queue Status</span>
                <span className="tech-mono text-[var(--text-primary)]">{job.queue_status || 'NONE'}</span>
              </div>
              <div className="flex justify-between items-center text-sm">
                <span className="text-[var(--text-tertiary)]">Lease Owner</span>
                <span className="tech-mono text-[var(--text-primary)] truncate max-w-[150px]">{job.lease_owner || 'NONE'}</span>
              </div>
              <div className="flex justify-between items-center text-sm">
                <span className="text-[var(--text-tertiary)]">Attempts</span>
                <span className="tech-mono text-[var(--text-primary)]">{job.attempt} / {job.max_attempts}</span>
              </div>
            </div>
          </div>
          
          {job.error_message && (
            <div className="border border-[var(--color-brand-error-muted)] bg-[var(--color-brand-error-muted)]/10 rounded-sm">
              <div className="p-3 border-b border-[var(--color-brand-error-muted)] flex items-center gap-2 text-[var(--color-brand-error)]">
                <ShieldAlert size={16} />
                <h3 className="font-semibold text-sm uppercase tracking-widest">Last Error</h3>
              </div>
              <div className="p-4">
                <p className="text-xs text-[var(--text-primary)] font-mono break-all leading-relaxed whitespace-pre-wrap">
                  {job.error_message}
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
