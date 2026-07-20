(function exposeJobState(global) {
  const ACTIVE_JOB_STATUSES = new Set(["queued", "running"]);
  const TERMINAL_JOB_STATUSES = new Set(["completed", "error", "cancelled"]);
  const HEX32 = /^[0-9a-f]{32}$/;

  global.CosmosJobState = Object.freeze({
    isActive(status) {
      return ACTIVE_JOB_STATUSES.has(status);
    },
    isTerminal(status) {
      return TERMINAL_JOB_STATUSES.has(status);
    },
    isRemoved(status) {
      return status === "removed";
    },
    isValidJobId(jobId) {
      return typeof jobId === "string" && HEX32.test(jobId);
    },
    /**
     * One-shot lifecycle: only the in-memory current job may open the task center.
     * Persisted job-id recovery is intentionally unsupported.
     */
    resolveTaskCenterJobId(currentJobId) {
      if (typeof currentJobId === "string" && HEX32.test(currentJobId)) {
        return currentJobId;
      }
      return null;
    },
    canOpenTaskCenter(hostAvailable, currentJobId) {
      return Boolean(
        hostAvailable && global.CosmosJobState.resolveTaskCenterJobId(currentJobId)
      );
    },
    /**
     * Whether the sidepanel should clear its in-memory job handle.
     * Terminal (completed/error/cancelled) and removed (post-cleanup) both end the session.
     */
    shouldClearCurrentJob(status) {
      return (
        global.CosmosJobState.isTerminal(status) ||
        global.CosmosJobState.isRemoved(status)
      );
    },
  });
})(globalThis);
