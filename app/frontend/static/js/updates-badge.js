// A red bubble on the Updates tab counting the scans that found changes or problems since the tab was last opened,
// which are marked "New" when it is opened.
// When it was last opened is kept in `localStorage`, which the desktop window keeps between launches, like the theme.
// Uses `selectDashboardTab` having already shown the right tab (dashboard-tabs.js).
// The dashboard's scripts share one scope, so they are loaded in order (see templates/index.html).

const UPDATES_SEEN_KEY = "inwebstigator-updates-seen-at";
const updatesTab = document.getElementById("updates-tab");
const updatesBadge = document.getElementById("updates-badge");
const updatesPanel = document.getElementById("updates-panel");

const scanCards = Array.from(document.querySelectorAll(".scan-change-record[data-scanned-at]"));
const scannedAt = (card) => Date.parse(card.dataset.scannedAt);
// Scan times come from the app, not this computer's clock, so a clock that is out does not hide new scans
const newestScanAt = Math.max(0, ...scanCards.map(scannedAt));

// When the newest scan the Updates tab showed was scanned, or null if that can't be remembered
function updatesSeenAt() {
    try {
        const saved = localStorage.getItem(UPDATES_SEEN_KEY);
        if (saved === null) {
            // The first time, the scans already there aren't counted as new
            localStorage.setItem(UPDATES_SEEN_KEY, String(newestScanAt));
            return newestScanAt;
        }
        return Number(saved);
    } catch (error) {
        return null;  // Storage unavailable, so no bubble, rather than one that never goes away
    }
}

const seenAt = updatesSeenAt();
const newScanCards = seenAt === null
    ? []
    : scanCards.filter((card) => card.dataset.foundSomething === "true" && scannedAt(card) > seenAt);

if (newScanCards.length) {
    updatesBadge.textContent = newScanCards.length > 99 ? "99+" : String(newScanCards.length);
    updatesBadge.hidden = false;
    updatesTab.setAttribute("aria-label", `Updates, ${newScanCards.length} new`);
}

// Once the Updates tab is shown, mark its new scans (and their websites, whose scans start hidden) and clear the
// bubble. The labels stay until the dashboard reloads, so it's clear which scans were new.
let updatesSeen = false;

function markUpdatesSeen() {
    if (updatesSeen || updatesPanel.hidden) {
        return;
    }
    updatesSeen = true;

    newScanCards.forEach((card) => {
        card.querySelector(".new-label").hidden = false;
        card.closest(".website-change-record")
            .querySelector(":scope > .change-record-header .new-label").hidden = false;
    });
    updatesBadge.hidden = true;
    updatesTab.removeAttribute("aria-label");

    try {
        localStorage.setItem(UPDATES_SEEN_KEY, String(newestScanAt));
    } catch (error) {
        // Not remembered, so the same scans are counted again after a reload
    }
}

// After dashboard-tabs.js has shown the tab, as its listeners were added first
updatesTab.addEventListener("click", markUpdatesSeen);
window.addEventListener("hashchange", markUpdatesSeen);
markUpdatesSeen();
