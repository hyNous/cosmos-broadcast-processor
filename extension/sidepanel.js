const HOST_NAME = "com.hynous.cosmos_broadcast_processor";
const EPISODE_PATTERN = /^https:\/\/www\.xiaoyuzhoufm\.com\/episode\/[A-Za-z0-9_-]+\/?(?:[?#].*)?$/;

const elements = Object.fromEntries(
  [
    "hostBadge", "episodeUrl", "parseButton", "episodeInfo", "cover",
    "episodeTitle", "episodeDuration", "startTime", "endTime", "speed",
    "volume", "outputDir", "browseButton", "filename", "downloadButton",
    "cancelButton", "openTaskCenterButton", "progressSection", "statusText",
    "progressText", "progressBar", "openFolderButton", "errorText",
  ].map((id) => [id, document.getElementById(id)])
);

// One-shot: job id lives only in this sidepanel JS lifetime (never chrome.storage).
let episode = null;
let currentJobId = null;
// Memory-only path for "open folder" after status is cleaned (not persisted).
let lastOutputPath = null;
let pollTimer = null;
let pollInFlight = false;
let hostAvailable = false;

function refreshTaskCenterButton() {
  elements.openTaskCenterButton.disabled = !CosmosJobState.canOpenTaskCenter(
    hostAvailable,
    currentJobId
  );
}

function clearCurrentJob() {
  currentJobId = null;
  refreshTaskCenterButton();
}

function sendNative(message) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendNativeMessage(HOST_NAME, message, (response) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      if (!response) {
        reject(new Error("本地辅助程序没有返回响应"));
        return;
      }
      if (!response.ok) {
        reject(new Error(response.error || "本地辅助程序执行失败"));
        return;
      }
      resolve(response);
    });
  });
}

function setError(error) {
  const message = error instanceof Error ? error.message : String(error);
  elements.errorText.textContent = message;
  elements.errorText.classList.remove("hidden");
}

function clearError() {
  elements.errorText.textContent = "";
  elements.errorText.classList.add("hidden");
}

function setBusy(busy) {
  for (const key of ["episodeUrl", "parseButton", "startTime", "endTime", "speed", "volume", "outputDir", "browseButton", "filename"]) {
    elements[key].disabled = busy;
  }
  elements.downloadButton.disabled = busy || !episode || !hostAvailable;
  elements.cancelButton.classList.toggle("hidden", !busy);
  refreshTaskCenterButton();
}

function secondsToTime(totalSeconds) {
  const seconds = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return [hours, minutes, seconds % 60].map((value) => String(value).padStart(2, "0")).join(":");
}

function timeToSeconds(value) {
  const match = /^(\d{1,3}):([0-5]\d):([0-5]\d)$/.exec(value.trim());
  if (!match) throw new Error("时间格式应为 HH:MM:SS，例如 01:23:45");
  return Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]);
}

async function detectHost() {
  try {
    const response = await sendNative({ command: "ping" });
    hostAvailable = true;
    elements.hostBadge.textContent = "已连接";
    elements.hostBadge.className = "badge";
    if (!elements.outputDir.value) {
      elements.outputDir.value = response.default_output_dir;
      await chrome.storage.local.set({ outputDir: response.default_output_dir });
    }
    if (!response.ffmpeg_available) {
      setError("已连接辅助程序，但没有找到 FFmpeg。请安装 FFmpeg 并添加到 PATH。 ");
    }
  } catch (error) {
    hostAvailable = false;
    elements.hostBadge.textContent = "未安装";
    elements.hostBadge.className = "badge offline";
    setError(`无法连接本地辅助程序：${error.message}。请重新运行安装包中的“安装本地程序.cmd”，再完全重启浏览器。`);
  }
  elements.downloadButton.disabled = !hostAvailable || !episode;
  refreshTaskCenterButton();
}

async function getActiveEpisodeUrl() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab?.url && EPISODE_PATTERN.test(tab.url) ? tab.url : "";
}

async function parseEpisode() {
  clearError();
  const url = elements.episodeUrl.value.trim();
  if (!url) {
    setError("请先打开或输入一个小宇宙单集链接。");
    return;
  }
  setBusy(true);
  elements.progressSection.classList.remove("hidden");
  elements.statusText.textContent = "正在解析页面…";
  elements.progressText.textContent = "";
  elements.progressBar.removeAttribute("value");
  try {
    const response = await sendNative({ command: "parse", url });
    episode = response.episode;
    elements.episodeUrl.value = episode.episode_url;
    elements.episodeTitle.textContent = episode.title;
    elements.episodeDuration.textContent = episode.duration ? `时长 ${secondsToTime(episode.duration)}` : "未能自动探测时长";
    elements.cover.src = episode.cover || "";
    elements.cover.classList.toggle("hidden", !episode.cover);
    elements.episodeInfo.classList.remove("hidden");
    elements.startTime.value = "00:00:00";
    elements.endTime.value = secondsToTime(episode.duration);
    elements.statusText.textContent = "解析成功，可以开始下载";
    elements.progressText.textContent = "";
    elements.progressBar.value = 0;
  } catch (error) {
    episode = null;
    elements.episodeInfo.classList.add("hidden");
    elements.progressSection.classList.add("hidden");
    setError(error);
  } finally {
    setBusy(false);
  }
}

async function chooseDirectory() {
  clearError();
  try {
    const response = await sendNative({ command: "choose_directory", initial: elements.outputDir.value });
    elements.outputDir.value = response.path;
    await chrome.storage.local.set({ outputDir: response.path });
  } catch (error) {
    setError(error);
  }
}

async function startDownload() {
  clearError();
  if (!episode) return;
  try {
    const startSec = timeToSeconds(elements.startTime.value);
    const selectedEnd = timeToSeconds(elements.endTime.value);
    if (selectedEnd !== 0 && selectedEnd <= startSec) throw new Error("结束时间必须大于开始时间，或填 00:00:00 表示处理到结尾");
    const endSec = episode.duration > 0 && selectedEnd >= episode.duration ? 0 : selectedEnd;
    const speed = Number(elements.speed.value);
    const volume = Number(elements.volume.value);
    if (speed < 0.5 || speed > 3) throw new Error("倍速必须在 0.5 到 3.0 之间");
    if (volume < 0.1 || volume > 3) throw new Error("音量必须在 0.1 到 3.0 之间");

    setBusy(true);
    elements.openFolderButton.classList.add("hidden");
    lastOutputPath = null;
    elements.progressSection.classList.remove("hidden");
    elements.progressBar.value = 0;
    elements.progressText.textContent = "0%";
    elements.statusText.textContent = "正在创建任务…";
    // Only ordinary prefs (outputDir) go to storage — never job ids or task metadata.
    await chrome.storage.local.set({ outputDir: elements.outputDir.value });
    const response = await sendNative({
      command: "start",
      episode_url: episode.episode_url,
      output_dir: elements.outputDir.value,
      filename: elements.filename.value.trim(),
      start_sec: startSec,
      end_sec: endSec,
      speed,
      volume,
    });
    currentJobId = response.job.job_id;
    refreshTaskCenterButton();
    startPolling();
  } catch (error) {
    setBusy(false);
    setError(error);
  }
}

async function pollJob() {
  if (!currentJobId || pollInFlight) return null;
  pollInFlight = true;
  try {
    const response = await sendNative({ command: "status", job_id: currentJobId });
    const job = response.job;
    elements.progressSection.classList.remove("hidden");
    if (CosmosJobState.isRemoved(job.status)) {
      stopPolling();
      setBusy(false);
      clearCurrentJob();
      elements.cancelButton.classList.add("hidden");
      if (!lastOutputPath) {
        elements.statusText.textContent = "任务状态已清理（一次性任务，无历史）";
      }
      return job.status;
    }
    elements.progressBar.value = job.progress || 0;
    elements.progressText.textContent = `${job.progress || 0}%`;
    elements.statusText.textContent = job.message || job.status;
    if (CosmosJobState.isTerminal(job.status)) {
      stopPolling();
      setBusy(false);
      if (job.status === "completed") {
        if (job.output_path) {
          lastOutputPath = job.output_path;
          elements.statusText.textContent = `已保存：${job.output_path}`;
          elements.openFolderButton.classList.remove("hidden");
        } else {
          elements.statusText.textContent = "处理完成";
        }
      } else if (job.status === "error") {
        setError(job.error || "任务处理失败");
      } else if (job.status === "cancelled") {
        elements.statusText.textContent = "已取消";
      }
      // Terminal ends the in-session job handle; do not persist any id.
      clearCurrentJob();
      elements.cancelButton.classList.add("hidden");
    }
    return job.status;
  } catch (error) {
    stopPolling();
    setBusy(false);
    setError(error);
    return null;
  } finally {
    pollInFlight = false;
  }
}

function startPolling() {
  stopPolling();
  setBusy(true);
  pollJob();
  pollTimer = setInterval(pollJob, 1000);
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

async function cancelJob() {
  if (!currentJobId) return;
  try {
    await sendNative({ command: "cancel", job_id: currentJobId });
    elements.statusText.textContent = "正在取消…";
  } catch (error) {
    setError(error);
  }
}

async function openOutputFolder() {
  try {
    if (currentJobId) {
      await sendNative({ command: "open_output", job_id: currentJobId });
      return;
    }
    // After one-shot cleanup the job file is gone; open parent of last known MP3 path.
    // lastOutputPath is memory-only and never written to chrome.storage.
    if (lastOutputPath) {
      // Host open_output requires a live job; surface a clear message with the path.
      setError(`任务状态已清理。文件位于：${lastOutputPath}`);
      return;
    }
    setError("没有可打开的输出路径。");
  } catch (error) {
    setError(error);
  }
}

async function openTaskCenter() {
  clearError();
  const jobId = CosmosJobState.resolveTaskCenterJobId(currentJobId);
  if (!hostAvailable || !jobId) {
    setError("没有可打开的进行中任务。请先提交下载（任务结束后不可回溯）。");
    refreshTaskCenterButton();
    return;
  }
  try {
    await sendNative({ command: "show_task_center", job_id: jobId });
  } catch (error) {
    setError(error);
  }
}

async function initialize() {
  // Only ordinary configuration is restored — never job_id / title / url / progress.
  const stored = await chrome.storage.local.get(["outputDir"]);
  elements.outputDir.value = stored.outputDir || "";
  // Drop any legacy keys from older builds (best-effort, never re-read as job state).
  try {
    await chrome.storage.local.remove(["currentJobId", "lastJobId"]);
  } catch (_err) {
    // ignore
  }
  currentJobId = null;
  lastOutputPath = null;
  await detectHost();
  refreshTaskCenterButton();
  // Closed sidepanel does not restore old tasks; background worker / open terminal continue.
  const activeUrl = await getActiveEpisodeUrl();
  if (activeUrl) {
    elements.episodeUrl.value = activeUrl;
    await parseEpisode();
  } else {
    setError("当前标签页不是小宇宙单集页面。请打开 /episode/ 链接后再试。");
  }
}

elements.parseButton.addEventListener("click", parseEpisode);
elements.browseButton.addEventListener("click", chooseDirectory);
elements.downloadButton.addEventListener("click", startDownload);
elements.cancelButton.addEventListener("click", cancelJob);
elements.openFolderButton.addEventListener("click", openOutputFolder);
elements.openTaskCenterButton.addEventListener("click", openTaskCenter);
window.addEventListener("beforeunload", stopPolling);

initialize().catch(setError);
