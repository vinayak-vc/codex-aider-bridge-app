import { SSEClient } from '/static/js/core/sse.js';
import { toast } from '/static/js/core/toast.js';

let _notifier = null;
const _taskStatuses = new Map();

function notifyTaskEvent(task) {
  const taskId = task?.id;
  if (!taskId) {
    return;
  }

  const status = String(task.status || '').trim().toLowerCase();
  const previous = _taskStatuses.get(taskId);
  if (!status || previous === status) {
    return;
  }
  _taskStatuses.set(taskId, status);

  const title = `Task ${taskId}`;
  const sounds = {
    running: 'launch',
    approved: 'approved',
    success: 'approved',
    rework: 'rework',
    retrying: 'rework',
    failed: 'failed',
    failure: 'failed',
    'dry-run': 'bridgeStarted',
  };
  const types = {
    running: 'info',
    approved: 'success',
    success: 'success',
    rework: 'warning',
    retrying: 'warning',
    failed: 'error',
    failure: 'error',
    'dry-run': 'info',
  };
  const labels = {
    running: 'started',
    approved: 'approved',
    success: 'completed',
    rework: 'needs rework',
    retrying: 'retrying after validation failure',
    failed: 'failed',
    failure: 'failed',
    'dry-run': 'simulated in dry-run mode',
  };

  toast(
    labels[status] ? `Task ${taskId} ${labels[status]}.` : `Task ${taskId} changed to ${status}.`,
    types[status] || 'info',
    3200,
    title,
    { sound: sounds[status] || 'bridgeStarted' },
  );
}

export function initEventNotifications() {
  if (_notifier) {
    return _notifier;
  }

  _notifier = new SSEClient('/api/run/stream');
  _notifier
    .on('start', data => {
      _taskStatuses.clear();
      toast(
        data.repo_root ? `Run started for ${data.repo_root}.` : 'A new run has started.',
        'info',
        3600,
        'Bridge Started',
        { sound: 'bridgeStarted' },
      );
    })
    .on('plan_ready', data => {
      const count = data.total_tasks ?? data.task_count ?? '?';
      toast(`Plan ready with ${count} tasks.`, 'info', 3200, 'Plan Ready', { sound: 'launch' });
    })
    .on('task_update', data => notifyTaskEvent(data.task || data))
    .on('review_required', data => {
      const taskId = data.task_id || '?';
      toast(
        data.validation_message || `Review required for task ${taskId}.`,
        'warning',
        0,
        `Review Required`,
        { sound: 'reviewRequired' },
      );
    })
    .on('relay_review_needed', data => {
      const taskId = data.task_id || '?';
      toast(
        `AI Relay is waiting for a review decision on task ${taskId}.`,
        'warning',
        0,
        'AI Relay Review',
        { sound: 'reviewRequired' },
      );
    })
    .on('paused', () => {
      toast('Run paused.', 'warning', 3000, 'Bridge Paused', { sound: 'reviewRequired' });
    })
    .on('resumed', () => {
      toast('Run resumed.', 'success', 2600, 'Bridge Resumed', { sound: 'approved' });
    })
    .on('complete', data => {
      const status = String(data.status || '').toLowerCase();
      if (status === 'failure') {
        toast('Run finished with failures.', 'error', 0, 'Run Failed', { sound: 'error' });
      } else if (status === 'stopped') {
        toast('Run stopped.', 'warning', 3200, 'Run Stopped', { sound: 'stopped' });
      } else {
        toast('Run completed successfully.', 'success', 3600, 'Run Complete', { sound: 'success' });
      }
    })
    .on('stopped', () => {
      toast('Run stopped.', 'warning', 3200, 'Run Stopped', { sound: 'stopped' });
    })
    .on('error', data => {
      toast(data.message || 'The bridge reported an error.', 'error', 0, 'Bridge Error', { sound: 'error' });
    })
    .connect();

  window.addEventListener('beforeunload', () => _notifier?.disconnect(), { once: true });
  return _notifier;
}
