import { useState } from 'react';
import { AlertTriangle, CheckCircle, Search, ArrowRight, Loader2, ListPlus } from 'lucide-react';
import { StatusBadge } from './StatusBadge';
import { Link } from 'react-router-dom';

export function QuickCreateJob() {
  const [text, setText] = useState('');
  const [status, setStatus] = useState<'IDLE' | 'VALIDATING' | 'PREVIEW' | 'CREATING' | 'SUCCESS'>('IDLE');
  const [validations, setValidations] = useState<any[]>([]);
  const [results, setResults] = useState<any[]>([]);
  const [isFocused, setIsFocused] = useState(false);

  const getUrls = () => text.split('\n').map(s => s.trim()).filter(s => s.length > 0);

  const handleValidate = async () => {
    const urls = getUrls();
    if (urls.length === 0) return;
    setStatus('VALIDATING');
    
    try {
      const results = await Promise.all(urls.map(async (url) => {
        try {
          const res = await fetch('/api/dashboard/jobs/validate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, force: false })
          });
          return await res.json();
        } catch (e: any) {
          return { url, valid: false, is_duplicate: false, warnings: [e.message || 'Network error'] };
        }
      }));
      setValidations(results);
      setStatus('PREVIEW');
    } catch (e: any) {
      setStatus('IDLE');
    }
  };

  const handleCreate = async () => {
    setStatus('CREATING');
    try {
      const validUrls = validations.filter(v => v.valid);
      const responses = await Promise.all(validUrls.map(async (v) => {
        try {
          const res = await fetch('/api/dashboard/jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: v.url, force: v.is_duplicate }) // Force if duplicate was acknowledged
          });
          return await res.json();
        } catch (e: any) {
          return { success: false, error: e.message, url: v.url };
        }
      }));
      setResults(responses);
      setStatus('SUCCESS');
    } catch (e: any) {
      setStatus('PREVIEW');
    }
  };

  const reset = () => {
    setText('');
    setStatus('IDLE');
    setValidations([]);
    setResults([]);
  };

  const validCount = validations.filter(v => v.valid && !v.is_duplicate).length;
  const dupCount = validations.filter(v => v.is_duplicate).length;
  const errorCount = validations.filter(v => !v.valid).length;

  return (
    <div className="surface rounded-sm overflow-hidden mb-8">
      <div className="p-4 border-b border-[var(--border-subtle)] bg-[var(--bg-elevated)]/30 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ListPlus size={16} className="text-[var(--text-tertiary)]" />
          <h3 className="font-semibold text-sm uppercase tracking-widest text-[var(--text-secondary)]">Batch Create Jobs</h3>
        </div>
      </div>
      
      <div className="p-5">
        {status === 'IDLE' || status === 'VALIDATING' ? (
          <div className="flex flex-col gap-4">
            <div className={`relative border rounded-sm transition-colors ${isFocused ? 'border-[var(--color-brand-info)]' : 'border-[var(--border-strong)]'}`}>
              <textarea
                placeholder="Paste Facebook Reel URLs here... (One per line)"
                value={text}
                onChange={(e) => setText(e.target.value)}
                onFocus={() => setIsFocused(true)}
                onBlur={() => setIsFocused(false)}
                disabled={status === 'VALIDATING'}
                className="w-full bg-[var(--bg-app)] pl-4 pr-4 py-3 text-sm text-[var(--text-primary)] focus:outline-none font-mono resize-y min-h-[120px]"
              />
              <div className="absolute bottom-3 right-3 flex items-center gap-2 text-xs font-mono text-[var(--text-tertiary)]">
                {getUrls().length} URL(s) detected
              </div>
            </div>
            <div className="flex justify-end">
              <button
                onClick={handleValidate}
                disabled={status === 'VALIDATING' || getUrls().length === 0}
                className="px-6 py-2.5 bg-[var(--color-brand-info)] text-white rounded-sm text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2 min-w-[120px]"
              >
                {status === 'VALIDATING' ? <Loader2 size={16} className="animate-spin" /> : <Search size={16} />}
                {status === 'VALIDATING' ? 'Validating...' : 'Validate All'}
              </button>
            </div>
          </div>
        ) : status === 'PREVIEW' ? (
          <div className="space-y-6 animate-in fade-in duration-300">
            <div className="grid grid-cols-3 gap-4 border-b border-[var(--border-subtle)] pb-6">
              <div className="p-4 bg-[var(--bg-elevated)] rounded-sm border border-[var(--border-subtle)] text-center">
                <div className="text-2xl font-light tech-mono text-[var(--color-brand-success)]">{validCount}</div>
                <div className="text-xs text-[var(--text-secondary)] uppercase mt-1">Valid & Ready</div>
              </div>
              <div className="p-4 bg-[var(--color-brand-warning-muted)]/20 rounded-sm border border-[var(--color-brand-warning)]/30 text-center">
                <div className="text-2xl font-light tech-mono text-[var(--color-brand-warning)]">{dupCount}</div>
                <div className="text-xs text-[var(--text-secondary)] uppercase mt-1">Duplicates Detected</div>
              </div>
              <div className="p-4 bg-[var(--color-brand-error-muted)]/20 rounded-sm border border-[var(--color-brand-error)]/30 text-center">
                <div className="text-2xl font-light tech-mono text-[var(--color-brand-error)]">{errorCount}</div>
                <div className="text-xs text-[var(--text-secondary)] uppercase mt-1">Invalid Format</div>
              </div>
            </div>

            <div className="max-h-[300px] overflow-y-auto space-y-2 pr-2">
              {validations.map((v, i) => (
                <div key={i} className={`p-3 text-sm flex flex-col gap-2 rounded-sm border ${
                  !v.valid ? 'bg-[var(--color-brand-error-muted)]/10 border-[var(--color-brand-error)]/30 text-[var(--color-brand-error)]' 
                  : v.is_duplicate ? 'bg-[var(--color-brand-warning-muted)]/10 border-[var(--color-brand-warning)]/30' 
                  : 'bg-[var(--bg-elevated)] border-[var(--border-subtle)]'
                }`}>
                  <div className="flex items-center gap-2 w-full">
                    {!v.valid ? <AlertTriangle size={14} className="shrink-0" /> 
                    : v.is_duplicate ? <AlertTriangle size={14} className="text-[var(--color-brand-warning)] shrink-0" />
                    : <CheckCircle size={14} className="text-[var(--color-brand-success)] shrink-0" />}
                    <span className="font-mono truncate flex-1">{v.url}</span>
                  </div>
                  
                  {v.is_duplicate && v.existing_job && (
                     <div className="ml-5 flex items-center gap-3 text-xs text-[var(--text-secondary)] bg-black/20 p-2 rounded-sm w-fit border border-[var(--border-subtle)]">
                       <span className="text-[var(--color-brand-warning)] font-bold">Duplicate</span>
                       <Link to={`/jobs/${v.existing_job.job_id}`} className="text-[var(--color-brand-info)] hover:underline tech-mono">
                         {v.existing_job.short_id}
                       </Link>
                       <StatusBadge status={v.existing_job.status} />
                     </div>
                  )}
                  
                  {!v.valid && v.warnings && (
                    <div className="ml-5 text-xs opacity-80">{v.warnings.join(' ')}</div>
                  )}
                </div>
              ))}
            </div>

            <div className="flex justify-end gap-3 pt-4 border-t border-[var(--border-subtle)]">
              <button onClick={reset} className="px-4 py-2 text-sm text-[var(--text-secondary)] hover:text-white hover:bg-[var(--bg-elevated)] rounded-sm transition-colors">
                Cancel
              </button>
              
              <button 
                onClick={handleCreate} 
                disabled={validCount === 0 && dupCount === 0}
                className="px-6 py-2 bg-[var(--color-brand-info)] text-white rounded-sm text-sm font-medium hover:opacity-90 transition-opacity flex items-center gap-2 disabled:opacity-50"
              >
                Create {validCount + dupCount} Job(s) <ArrowRight size={16} />
              </button>
            </div>
          </div>
        ) : status === 'SUCCESS' ? (
          <div className="flex flex-col items-center justify-center py-6 animate-in zoom-in-95 duration-300">
            <div className="w-12 h-12 rounded-full bg-[var(--color-brand-success-muted)] flex items-center justify-center mb-4">
              <CheckCircle size={24} className="text-[var(--color-brand-success)]" />
            </div>
            <h3 className="text-lg font-medium">Batch Creation Complete</h3>
            <p className="text-sm text-[var(--text-secondary)] mt-1 mb-6">
              {results.filter(r => r.success).length} jobs created successfully.
            </p>
            
            <div className="flex gap-4">
              <button onClick={reset} className="px-4 py-2 text-sm text-[var(--text-secondary)] hover:text-white hover:bg-[var(--bg-elevated)] rounded-sm border border-[var(--border-strong)] transition-colors">
                Start Over
              </button>
              <Link to="/jobs" className="px-6 py-2 bg-[var(--color-brand-info)] text-white rounded-sm text-sm font-medium hover:opacity-90 transition-opacity flex items-center gap-2">
                View Queue <ArrowRight size={16} />
              </Link>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
