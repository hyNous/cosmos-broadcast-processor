const EPISODE_PATTERN = /^https:\/\/www\.xiaoyuzhoufm\.com\/episode\/[A-Za-z0-9_-]+\/?(?:[?#].*)?$/;

async function configureTab(tabId, url) {
  const enabled = typeof url === "string" && EPISODE_PATTERN.test(url);
  try {
    await chrome.sidePanel.setOptions({
      tabId,
      path: "sidepanel.html",
      enabled,
    });
  } catch (error) {
    console.debug("Unable to configure side panel", error);
  }
}

chrome.runtime.onInstalled.addListener(async () => {
  await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  const tabs = await chrome.tabs.query({});
  await Promise.all(tabs.map((tab) => configureTab(tab.id, tab.url)));
});

chrome.runtime.onStartup.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.url || changeInfo.status === "complete") {
    configureTab(tabId, tab.url);
  }
});

chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  const tab = await chrome.tabs.get(tabId);
  await configureTab(tabId, tab.url);
});
