/**
 * runner.ts
 * Manages a single long-running `python main.py` subprocess.
 * Only one job can run at a time (the bridge itself is single-job per repo).
 */
export interface RunFlags {
    aider_model?: string;
    validation_command?: string;
    auto_split_threshold?: number;
    manual_supervisor?: boolean;
    aider_no_map?: boolean;
    task_timeout?: number;
    workflow_profile?: string;
    log_level?: string;
}
export interface JobInfo {
    pid: number;
    repo_root: string;
    plan_file: string;
    log_file: string;
    started_at: string;
    status: 'running' | 'done' | 'failed' | 'cancelled';
    exit_code: number | null;
}
export declare function currentJob(): JobInfo | null;
/** Detect bridge root by scanning up from this file for main.py */
export declare function detectBridgeRoot(): string;
export interface DryRunResult {
    valid: boolean;
    task_count: number;
    tasks_preview: Array<{
        id: number;
        instruction: string;
    }>;
    errors: string[];
    rollback_sha: string | null;
    raw_exit_code: number;
}
export declare function runDryRun(repoRoot: string, planFile: string, goal?: string, flags?: RunFlags): Promise<DryRunResult>;
export interface StartResult {
    started: boolean;
    pid: number;
    log_file: string;
    rollback_sha: string | null;
    error?: string;
}
export declare function startRun(repoRoot: string, planFile: string, goal: string, flags?: RunFlags): StartResult;
export declare function cancelRun(): {
    cancelled: boolean;
    pid: number | null;
    error?: string;
};
export declare function tailLog(logFile: string, lines?: number): string[];
