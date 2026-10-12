// Keeps each website card's scan progress up to date while the dashboard is open, without reloading it: which website
// is being scanned now, which are waiting their turn, and which have just finished. That includes scans started
// somewhere else, such as scheduled scans, Run All Scans or another window.
// Uses the Run All Scans elements and `runAllStartedHere` (scans.js).
// The dashboard's scripts share one scope, so they are loaded in order (see templates/index.html).

const SCAN_ACTIVITY_CHECK_MS = 3000;
const SCANNING_NOW_TEXT = "Scanning now...";
const WAITING_TEXT = "Scan queued. It runs once the scan in progress has finished...";
const SCAN_FINISHED_TEXT = "Scan finished. Press Refresh to see what it found.";
const RUN_ALL_FINISHED_TEXT = "Run All Scans finished. Press Refresh to see the latest results.";

function websiteCardFor(url) {
    const runButton = document.querySelector(`.website-card .run-scan-button[data-website-url="${CSS.escape(url)}"]`);
    return runButton ? runButton.closest(".website-card") : null;
}

// The websites shown as being scanned or waiting, to spot the scans that finish while the page is open
let activeScanUrls = new Set(
    Array.from(document.querySelectorAll(".website-card .run-scan-message.is-busy"))
        .map((message) => message.closest(".website-card").querySelector(".run-scan-button").dataset.websiteUrl)
);

// Whether Run All Scans was running at the last check, to spot it finishing
let runAllWasRunning = Boolean(cancelAllScansButton && !cancelAllScansButton.hidden);

function showScanInProgress(card, text) {
    const message = card.querySelector(".run-scan-message");
    // Only changed when it differs, as the background scan note watches the card for changes (scans.js)
    if (message.textContent !== text) {
        message.textContent = text;
    }
    message.classList.add("is-busy");
    card.querySelector(".run-scan-button").disabled = true;
    card.querySelector(".cancel-scan-button").hidden = false;
}

function showScanFinished(card) {
    const message = card.querySelector(".run-scan-message");
    // A scan run or cancelled from this page has already said how it ended (scans.js)
    if (!message.classList.contains("is-busy")) {
        return;
    }
    message.classList.remove("is-busy");
    message.textContent = SCAN_FINISHED_TEXT;
    card.querySelector(".run-scan-button").disabled = false;
    card.querySelector(".cancel-scan-button").hidden = true;
}

function showRunAllActivity(activity) {
    // Left alone while it is being cancelled, as the cancel says how that went (scans.js)
    if (!runAllScansButton || cancelAllScansButton.disabled) {
        return;
    }

    if (activity.run_all_in_progress) {
        const scanningCard = activity.scanning_url ? websiteCardFor(activity.scanning_url) : null;
        const text = scanningCard
            ? `Scanning every website. Now scanning ${scanningCard.dataset.websiteName}...`
            : "Scanning every website. Large websites can take several minutes...";
        if (runAllScansMessage.textContent !== text) {
            runAllScansMessage.textContent = text;
        }
        runAllScansMessage.classList.add("is-busy");
        runAllScansButton.disabled = true;
        cancelAllScansButton.hidden = false;
    } else if (runAllWasRunning && !runAllStartedHere) {
        // One started here reloads the page by itself when it ends (scans.js)
        runAllScansMessage.classList.remove("is-busy");
        runAllScansMessage.textContent = RUN_ALL_FINISHED_TEXT;
        runAllScansButton.disabled = false;
        cancelAllScansButton.hidden = true;
    }
    runAllWasRunning = activity.run_all_in_progress;
}

async function checkScanActivity() {
    let activity;
    try {
        const response = await fetch("/scanner/activity");
        if (!response.ok) {
            return;
        }
        activity = await response.json();
    } catch (error) {
        return;  // e.g. the app is quitting. The next check tries again.
    }

    const nowActive = new Set(activity.waiting_urls);
    if (activity.scanning_url) {
        nowActive.add(activity.scanning_url);
    }

    // Websites being added have no card yet, so only those with one are shown
    for (const url of nowActive) {
        const card = websiteCardFor(url);
        if (card) {
            showScanInProgress(card, url === activity.scanning_url ? SCANNING_NOW_TEXT : WAITING_TEXT);
        }
    }
    for (const url of activeScanUrls) {
        const card = nowActive.has(url) ? null : websiteCardFor(url);
        if (card) {
            showScanFinished(card);
        }
    }
    activeScanUrls = nowActive;

    showRunAllActivity(activity);
}

// Not checked while the window is hidden (e.g. closed to the system tray), and checked straight away when it is shown
setInterval(() => {
    if (!document.hidden) {
        checkScanActivity();
    }
}, SCAN_ACTIVITY_CHECK_MS);

document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
        checkScanActivity();
    }
});
