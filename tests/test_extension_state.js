"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

require("../extension/job-state.js");

assert.equal(CosmosJobState.isActive("queued"), true);
assert.equal(CosmosJobState.isActive("running"), true);
for (const status of ["completed", "error", "cancelled", "removed", null, undefined, "unknown"]) {
  assert.equal(CosmosJobState.isActive(status), false, `${status} must not block episode parsing`);
}
for (const status of ["completed", "error", "cancelled"]) {
  assert.equal(CosmosJobState.isTerminal(status), true);
}
assert.equal(CosmosJobState.isTerminal("running"), false);
assert.equal(CosmosJobState.isTerminal("removed"), false);
assert.equal(CosmosJobState.isRemoved("removed"), true);
assert.equal(CosmosJobState.isRemoved("completed"), false);

const goodId = "a".repeat(32);
const otherId = "b".repeat(32);
assert.equal(CosmosJobState.isValidJobId(goodId), true);
assert.equal(CosmosJobState.isValidJobId("ABC"), false);
assert.equal(CosmosJobState.isValidJobId(""), false);
assert.equal(CosmosJobState.isValidJobId(null), false);

// One-shot: only in-memory currentJobId; lastJobId is not accepted.
assert.equal(CosmosJobState.resolveTaskCenterJobId(goodId), goodId);
assert.equal(CosmosJobState.resolveTaskCenterJobId(null), null);
assert.equal(CosmosJobState.resolveTaskCenterJobId(undefined), null);
assert.equal(CosmosJobState.resolveTaskCenterJobId("nope"), null);
// Extra args (legacy lastJobId) must be ignored if callers still pass them.
assert.equal(CosmosJobState.resolveTaskCenterJobId(null, otherId), null);

assert.equal(CosmosJobState.canOpenTaskCenter(true, goodId), true);
assert.equal(CosmosJobState.canOpenTaskCenter(true, null), false);
assert.equal(CosmosJobState.canOpenTaskCenter(true, null, otherId), false);
assert.equal(CosmosJobState.canOpenTaskCenter(false, goodId), false);

assert.equal(CosmosJobState.shouldClearCurrentJob("completed"), true);
assert.equal(CosmosJobState.shouldClearCurrentJob("error"), true);
assert.equal(CosmosJobState.shouldClearCurrentJob("cancelled"), true);
assert.equal(CosmosJobState.shouldClearCurrentJob("removed"), true);
assert.equal(CosmosJobState.shouldClearCurrentJob("running"), false);
assert.equal(CosmosJobState.shouldClearCurrentJob("queued"), false);

// show_task_center payload shape used by sidepanel — current only.
function buildShowPayload(currentJobId) {
  const jobId = CosmosJobState.resolveTaskCenterJobId(currentJobId);
  if (!jobId) return null;
  return { command: "show_task_center", job_id: jobId };
}
assert.deepEqual(buildShowPayload(goodId), {
  command: "show_task_center",
  job_id: goodId,
});
assert.equal(buildShowPayload(null), null);

// Source-level: sidepanel must not persist job ids.
const sidepanelPath = path.join(__dirname, "..", "extension", "sidepanel.js");
const sidepanel = fs.readFileSync(sidepanelPath, "utf8");
// lastJobId may appear only as a legacy storage key to delete — never as a live variable.
assert.equal(
  /\blet\s+lastJobId\b/.test(sidepanel),
  false,
  "lastJobId must not be a live variable"
);
assert.equal(
  /\blastJobId\s*=/.test(sidepanel),
  false,
  "lastJobId must not be assigned"
);
assert.equal(
  /chrome\.storage\.local\.set\(\s*\{\s*currentJobId/.test(sidepanel),
  false,
  "currentJobId must not be written to chrome.storage.local"
);
assert.equal(
  /chrome\.storage\.local\.set\(\s*\{\s*lastJobId/.test(sidepanel),
  false,
  "lastJobId must not be written to chrome.storage.local"
);
assert.equal(
  /chrome\.storage\.local\.get\(\[[^\]]*currentJobId/.test(sidepanel),
  false,
  "currentJobId must not be read from storage"
);
assert.equal(
  /chrome\.storage\.local\.get\(\[[^\]]*lastJobId/.test(sidepanel),
  false,
  "lastJobId must not be read from storage"
);
// storage.local may only keep ordinary prefs like outputDir (and legacy key cleanup).
assert.match(sidepanel, /chrome\.storage\.local\.set\(\{\s*outputDir:/);
assert.match(sidepanel, /chrome\.storage\.local\.remove\(\["currentJobId",\s*"lastJobId"\]\)/);
assert.match(sidepanel, /let currentJobId = null/);
assert.match(sidepanel, /isRemoved/);
assert.match(sidepanel, /任务状态已清理/);

const jobStatePath = path.join(__dirname, "..", "extension", "job-state.js");
const jobState = fs.readFileSync(jobStatePath, "utf8");
assert.equal(jobState.includes("lastJobId"), false, "job-state must not reference lastJobId");
assert.match(jobState, /isRemoved/);
assert.match(jobState, /shouldClearCurrentJob/);

console.log("extension job-state decisions: OK");
