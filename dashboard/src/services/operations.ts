export interface OperationPolicy {
    allowed: boolean;
    operation: string;
    reason?: string;
    risk: string;
}

export interface OperationPreview {
    operation: string;
    allowed: boolean;
    risk: string;
    from_state: string;
    attempt: number;
    next_attempt: number;
    reason?: string;
}


const OPS_BASE = '/api/operations/jobs';

export const getOperations = async (jobId: string): Promise<OperationPolicy[]> => {
    const res = await fetch(`${OPS_BASE}/${jobId}/allowed`);
    if (!res.ok) throw new Error('Failed to fetch operations');
    const data = await res.json();
    return data.allowed_operations;
};

export const previewOperation = async (jobId: string, operation: string): Promise<OperationPreview> => {
    const res = await fetch(`${OPS_BASE}/${jobId}/preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ operation })
    });
    if (!res.ok) throw new Error('Failed to preview operation');
    return res.json();
};

export const executeOperation = async (jobId: string, operation: string, reason: string): Promise<any> => {
    const idempotencyKey = `${operation}-${jobId}-${Date.now()}`;
    const operationId = `op-${Date.now()}`;
    
    let path = '';
    switch (operation) {
        case 'RETRY': path = 'retry'; break;
        case 'RESUME': path = 'resume'; break;
        case 'RECONCILE': path = 'reconcile'; break;
        case 'CANCEL': path = 'cancel'; break;
        case 'MARK_MANUAL_REVIEW': path = 'manual-review'; break;
    }

    const res = await fetch(`${OPS_BASE}/${jobId}/${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            operation_id: operationId,
            idempotency_key: idempotencyKey,
            reason,
            operator: 'admin'
        })
    });
    
    if (!res.ok) {
        const error = await res.json();
        throw new Error(error.detail?.message || 'Operation failed');
    }
    
    return res.json();
};
