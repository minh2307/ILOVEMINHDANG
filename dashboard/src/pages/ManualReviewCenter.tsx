import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { StatusBadge } from '../components/ui/StatusBadge';
import { Eye, CheckSquare, Image as ImageIcon, ShieldAlert, ArrowRight, RotateCw, Globe } from 'lucide-react';
import { previewOperation, executeOperation } from '../services/operations';

export default function ManualReviewCenter() {
  const [jobs, setJobs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedJob, setSelectedJob] = useState<any | null>(null);

  const fetchJobs = async () => {
    try {
      const res = await fetch('/api/dashboard/jobs?status=FACEBOOK_PUBLISH_UNCERTAIN');
      const data = await res.json();
      const reviewJobs = (data.items || data).filter((j: any) => 
        j.status.includes('UNCERTAIN') || j.status.includes('MANUAL_REVIEW') || j.status.includes('RECONCILE')
      );
      setJobs(reviewJobs);
      if (reviewJobs.length > 0 && !selectedJob) {
        setSelectedJob(reviewJobs[0]);
      }
    } catch (err) {}
    setLoading(false);
  };

  useEffect(() => {
    fetchJobs();
    const interval = setInterval(fetchJobs, 10000);
    return () => clearInterval(interval);
  }, []);

  const handleReconcile = async (action: string) => {
    if (!selectedJob) return;
    try {
      const preview = await previewOperation(selectedJob.job_id, action);
      if (preview.allowed) {
        await executeOperation(selectedJob.job_id, action, "Operator reconciled via Manual Review Center");
        fetchJobs();
        setSelectedJob(null);
      }
    } catch (err) {
      alert("Failed to execute operation: " + err);
    }
  };

  if (loading && jobs.length === 0) {
    return <div className="flex items-center justify-center h-64 tech-mono text-[var(--text-tertiary)] animate-pulse">Loading Review Center...</div>;
  }

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-medium tracking-tight">Manual Review Center</h2>
          <p className="text-[var(--text-secondary)] mt-1">Audit uncertain operations and resolve anomalies</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="bg-[var(--color-brand-warning-muted)] text-[var(--color-brand-warning)] px-3 py-1.5 rounded-sm font-bold tech-mono text-sm border border-[var(--color-brand-warning-muted)]">
            {jobs.length} jobs require attention
          </div>
        </div>
      </div>

      <div className="flex flex-col lg:flex-row gap-6 h-[calc(100vh-160px)]">
        {/* Left Column: Job List */}
        <div className="w-full lg:w-1/3 flex flex-col surface rounded-sm border border-[var(--border-strong)] overflow-hidden">
          <div className="p-4 border-b border-[var(--border-subtle)] bg-[var(--bg-app)] flex items-center justify-between">
            <h3 className="font-semibold text-sm uppercase tracking-widest text-[var(--text-secondary)]">Queue</h3>
            <button onClick={fetchJobs} className="text-[var(--text-tertiary)] hover:text-white transition-colors"><RotateCw size={14} /></button>
          </div>
          <div className="flex-1 overflow-y-auto divide-y divide-[var(--border-subtle)]">
            {jobs.length === 0 ? (
              <div className="p-8 text-center text-[var(--text-tertiary)] tech-mono text-sm">All clear. No reviews pending.</div>
            ) : jobs.map(job => (
              <div 
                key={job.job_id} 
                onClick={() => setSelectedJob(job)}
                className={`p-4 cursor-pointer transition-colors border-l-2 ${
                  selectedJob?.job_id === job.job_id 
                    ? 'bg-[var(--bg-elevated)] border-l-[var(--color-brand-warning)]' 
                    : 'hover:bg-[var(--bg-elevated)]/50 border-l-transparent'
                }`}
              >
                <div className="flex justify-between items-start mb-2">
                  <span className="tech-mono text-[var(--color-brand-info)] font-bold">{job.short_id}</span>
                  <StatusBadge status={job.status} />
                </div>
                <div className="text-sm text-[var(--text-secondary)] truncate mb-2">{job.source_url}</div>
                <div className="text-xs text-[var(--text-tertiary)] tech-mono flex items-center justify-between">
                  <span>Stage: {job.stage}</span>
                  <span>Attempt {job.attempt}</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Right Column: Evidence Viewer */}
        <div className="w-full lg:w-2/3 flex flex-col surface rounded-sm border border-[var(--border-strong)] overflow-hidden">
          {selectedJob ? (
            <>
              <div className="p-4 border-b border-[var(--border-subtle)] bg-[var(--bg-app)] flex items-center justify-between shrink-0">
                <div className="flex items-center gap-3">
                  <ShieldAlert size={18} className="text-[var(--color-brand-warning)]" />
                  <h3 className="font-medium">Evidence Viewer</h3>
                  <span className="tech-mono text-xs text-[var(--text-tertiary)] ml-2">Job {selectedJob.short_id}</span>
                </div>
                <Link to={`/jobs/${selectedJob.job_id}`} className="text-xs tech-mono text-[var(--color-brand-info)] hover:underline flex items-center gap-1">
                  Full Details <ArrowRight size={12} />
                </Link>
              </div>
              
              <div className="flex-1 overflow-y-auto p-6 space-y-6 bg-[var(--bg-app)]">
                {/* Last Error Context */}
                {selectedJob.error_message && (
                  <div className="p-4 bg-[var(--color-brand-warning-muted)]/20 border border-[var(--color-brand-warning)]/30 rounded-sm">
                    <h4 className="text-sm font-bold text-[var(--color-brand-warning)] uppercase tracking-wider mb-2">Anomaly Detected</h4>
                    <p className="text-sm font-mono text-[var(--text-secondary)] break-all whitespace-pre-wrap">{selectedJob.error_message}</p>
                  </div>
                )}
                
                {/* Evidence Grid */}
                <div className="grid grid-cols-2 gap-4">
                  <div className="surface p-4 rounded-sm">
                    <h4 className="flex items-center gap-2 text-sm font-semibold text-[var(--text-secondary)] mb-3">
                      <ImageIcon size={16} /> Screenshot Snapshot
                    </h4>
                    <div className="aspect-video bg-black/50 border border-[var(--border-subtle)] rounded-sm flex items-center justify-center overflow-hidden relative group">
                      <img 
                        src={`/data/jobs/${selectedJob.job_id}/screenshots/error_state.png`} 
                        alt="Error state screenshot"
                        className="w-full h-full object-contain"
                        onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                      />
                      <div className="absolute inset-0 flex items-center justify-center text-[var(--text-tertiary)] tech-mono text-xs opacity-100 group-hover:opacity-0 transition-opacity" style={{ zIndex: -1 }}>
                        No screenshot available
                      </div>
                    </div>
                  </div>
                  
                  <div className="surface p-4 rounded-sm flex flex-col">
                    <h4 className="flex items-center gap-2 text-sm font-semibold text-[var(--text-secondary)] mb-3">
                      <Globe size={16} /> URLs & Context
                    </h4>
                    <div className="space-y-3 flex-1">
                      <div>
                        <div className="text-[10px] uppercase text-[var(--text-tertiary)] mb-1">Source URL</div>
                        <a href={selectedJob.source_url} target="_blank" rel="noreferrer" className="text-xs text-[var(--color-brand-info)] hover:underline truncate block">{selectedJob.source_url}</a>
                      </div>
                      <div>
                        <div className="text-[10px] uppercase text-[var(--text-tertiary)] mb-1">Facebook Target</div>
                        <a href={selectedJob.permalink || "#"} target="_blank" rel="noreferrer" className="text-xs text-[var(--color-brand-info)] hover:underline truncate block">{selectedJob.permalink || "Not extracted"}</a>
                      </div>
                      <div>
                        <div className="text-[10px] uppercase text-[var(--text-tertiary)] mb-1">Time Elapsed</div>
                        <div className="text-xs tech-mono text-[var(--text-primary)]">{selectedJob.updated_at}</div>
                      </div>
                    </div>
                  </div>
                </div>
                
                {/* Action Bar */}
                <div className="mt-8 border-t border-[var(--border-subtle)] pt-6">
                  <h4 className="flex items-center gap-2 text-sm font-semibold text-[var(--text-secondary)] mb-4">
                    <CheckSquare size={16} /> Resolution Actions
                  </h4>
                  <div className="flex flex-wrap gap-3">
                    <button 
                      onClick={() => handleReconcile('RECONCILE')}
                      className="px-4 py-2 bg-[var(--color-brand-success)] text-white font-medium text-sm rounded-sm hover:opacity-90 transition-opacity"
                    >
                      Confirm Published (Mark Success)
                    </button>
                    <button 
                      onClick={() => handleReconcile('RETRY')}
                      className="px-4 py-2 bg-[var(--color-brand-warning)] text-black font-medium text-sm rounded-sm hover:opacity-90 transition-opacity"
                    >
                      Retry Publication (Force)
                    </button>
                    <button 
                      onClick={() => handleReconcile('CANCEL')}
                      className="px-4 py-2 bg-transparent border border-[var(--color-brand-error)] text-[var(--color-brand-error)] font-medium text-sm rounded-sm hover:bg-[var(--color-brand-error-muted)] transition-colors"
                    >
                      Abort Job (Cancel)
                    </button>
                  </div>
                </div>
              </div>
            </>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center text-[var(--text-tertiary)] p-8 text-center">
              <Eye size={48} className="mb-4 opacity-20" />
              <p className="font-medium text-[var(--text-secondary)]">Select a job to review evidence</p>
              <p className="text-sm mt-2">The Evidence Viewer provides screenshots, DOM state, and execution context for manual reconciliation.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
