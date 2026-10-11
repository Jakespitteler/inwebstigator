// Runs and cancels scans, and says that closing the window won't stop them.
// Uses `failureReason` (helpers.js) and `setCardExpanded` (collapsible-cards.js).
// The dashboard's scripts share one scope, so they are loaded in order (see templates/index.html).

// Run a scan manually for an existing website
const runScanButtons =
    document.querySelectorAll(
        ".run-scan-button"
    );

// Scans run one at a time, so only refresh once every scan started here has finished.
// Refreshing sooner would lose track of the scans still waiting.
let scansInProgress = 0;

runScanButtons.forEach((button) => {

    button.addEventListener(
        "click",
        async () => {

            const websiteUrl =
                button.dataset.websiteUrl;

            const message =
                button.closest(".website-card").querySelector(
                    ".run-scan-message"
                );

            const cancelButton =
                button.closest(".website-card").querySelector(
                    ".cancel-scan-button"
                );

            // Collapse the card while it scans, leaving its header and scan progress on show
            setCardExpanded(button.closest(".website-card"), false);

            button.disabled = true;
            cancelButton.hidden = false;
            message.classList.add("is-busy");
            message.textContent =
                "Scan queued. It runs once any scan already in progress has finished...";

            const formData =
                new FormData();

            formData.append(
                "url",
                websiteUrl
            );

            scansInProgress++;

            try {

                const response =
                    await fetch(
                        "/scanner/run",
                        {
                            method: "POST",
                            body: formData
                        }
                    );

                if (!response.ok) {
                    // The server explains a scan that was already queued or cancelled, or whose report
                    // could not be emailed
                    throw new Error(await failureReason(response, "Unable to complete scan."));
                }

                message.textContent =
                    "Scan completed.";

            } catch (error) {

                message.textContent =
                    error.message;

                button.disabled = false;
                return;

            } finally {
                scansInProgress--;
                cancelButton.hidden = true;
                message.classList.remove("is-busy");
            }

            if (scansInProgress === 0) {
                window.location.reload();
            }
        }
    );
});

// Scan every website and email each recipient one report of all the changes, as a scheduled run does.
// The server also restarts the countdown to the next scheduled check.
const runAllScansButton = document.getElementById("run-all-scans-button");
const cancelAllScansButton = document.getElementById("cancel-all-scans-button");
const runAllScansMessage = document.getElementById("run-all-scans-message");

// Whether this page started Run All Scans, so it reloads by itself when the run ends
let runAllStartedHere = false;

if (runAllScansButton) {
    runAllScansButton.addEventListener("click", async () => {
        runAllStartedHere = true;
        runAllScansButton.disabled = true;
        cancelAllScansButton.hidden = false;
        runAllScansMessage.classList.add("is-busy");
        runAllScansMessage.textContent = "Scanning every website. Large websites can take several minutes...";

        try {
            const response = await fetch("/scanner/run_all", { method: "POST" });
            if (!response.ok) {
                // 409 means Run All Scans is already running, which the server explains
                throw new Error(await failureReason(response, "Unable to complete the scans."));
            }
        } catch (error) {
            runAllStartedHere = false;
            runAllScansMessage.classList.remove("is-busy");
            runAllScansMessage.textContent = error.message;
            cancelAllScansButton.hidden = true;
            runAllScansButton.disabled = false;
            return;
        }

        // Also after cancelling, to show what the websites scanned before it found
        window.location.reload();
    });
}

// Stop Run All Scans part way through: the website being scanned is cancelled and the rest are skipped.
// Websites already scanned keep their results, and their changes are still emailed.
if (cancelAllScansButton) {
    cancelAllScansButton.addEventListener("click", async () => {
        cancelAllScansButton.disabled = true;
        runAllScansMessage.textContent = "Cancelling. Changes found so far are being saved and emailed...";

        try {
            const response = await fetch("/scanner/cancel_all", { method: "POST" });
            if (!response.ok) {
                throw new Error("Unable to cancel the scans.");
            }

            const cancelled = await response.json();

            if (!cancelled) {
                // It had already finished, so show its results
                window.location.reload();
            } else if (!runAllStartedHere) {
                // Started before this page loaded, so nothing here is waiting for it to finish
                runAllScansMessage.classList.remove("is-busy");
                runAllScansMessage.textContent = "Run All Scans cancelled. Press Refresh to see the latest results.";
                cancelAllScansButton.hidden = true;
            }
            // Otherwise the page reloads by itself once the run has stopped
        } catch (error) {
            runAllScansMessage.textContent = error.message;
            cancelAllScansButton.disabled = false;
        }
    });
}

// While any scan runs (a spinner is showing), say that closing the window won't stop it,
// unless "Don't show again" was clicked. That is remembered between launches, like the theme.
const backgroundScanNote = document.getElementById("background-scan-note");
const HIDE_BACKGROUND_SCAN_NOTE_KEY = "inwebstigator-hide-background-scan-note";

// Set by the cross, which hides the note only until the scans running now have finished
let backgroundScanNoteClosedForNow = false;

function backgroundScanNoteDismissed() {
    if (backgroundScanNote.dataset.dismissed === "true") {
        return true;
    }
    try {
        return localStorage.getItem(HIDE_BACKGROUND_SCAN_NOTE_KEY) === "true";
    } catch (error) {
        return false;  // Storage unavailable, so keep showing the note
    }
}

function updateBackgroundScanNote() {
    // A website being added has a progress line that says "scanning" until its first scan ends
    const scanning =
        Boolean(document.querySelector(".is-busy"))
        || Boolean(document.querySelector('#add-website-progress-list [data-state="scanning"]'));
    if (!scanning) {
        backgroundScanNoteClosedForNow = false;  // So the next scan shows it again
    }
    const show = scanning && !backgroundScanNoteClosedForNow && !backgroundScanNoteDismissed();

    // Only touch it when it changes, as the observer below would otherwise be woken up forever
    if (backgroundScanNote.hidden === show) {
        backgroundScanNote.hidden = !show;
    }
}

const hideBackgroundScanNoteButton = document.getElementById("hide-background-scan-note");

if (hideBackgroundScanNoteButton) {
    hideBackgroundScanNoteButton.addEventListener("click", () => {
        try {
            localStorage.setItem(HIDE_BACKGROUND_SCAN_NOTE_KEY, "true");
        } catch (error) {
            // Not remembered, but still hidden until the page reloads
        }
        backgroundScanNote.hidden = true;
        backgroundScanNote.dataset.dismissed = "true";
    });
}

const closeBackgroundScanNoteButton = document.getElementById("close-background-scan-note");

if (closeBackgroundScanNoteButton) {
    closeBackgroundScanNoteButton.addEventListener("click", () => {
        backgroundScanNoteClosedForNow = true;
        backgroundScanNote.hidden = true;
    });
}

if (backgroundScanNote) {
    // Progress lines are added (childList) and finished (data-state) without any class changing
    new MutationObserver(updateBackgroundScanNote).observe(
        document.querySelector(".websites-section"),
        { subtree: true, childList: true, attributes: true, attributeFilter: ["class", "hidden", "data-state"] }
    );
    updateBackgroundScanNote();
}

// Cancel a website's scan, whether it is still queued or already running.
// Only the cancel buttons on website cards: the Add Website wizard's has its own handler (add-website.js).
const cancelScanButtons =
    document.querySelectorAll(
        ".website-card .cancel-scan-button"
    );

cancelScanButtons.forEach((button) => {

    button.addEventListener(
        "click",
        async () => {

            const card =
                button.closest(".website-card");

            const message =
                card.querySelector(
                    ".run-scan-message"
                );

            const formData =
                new FormData();

            formData.append(
                "url",
                button.dataset.websiteUrl
            );

            button.disabled = true;

            try {

                const response =
                    await fetch(
                        "/scanner/cancel",
                        {
                            method: "POST",
                            body: formData
                        }
                    );

                if (!response.ok) {
                    throw new Error(
                        "Unable to cancel scan."
                    );
                }

                const cancelled =
                    await response.json();

                if (!cancelled) {
                    window.location.reload();
                    return;
                }

                message.classList.remove("is-busy");
                message.textContent =
                    "Scan cancelled.";

                button.hidden = true;
                card.querySelector(".run-scan-button").disabled = false;

            } catch (error) {

                message.textContent =
                    error.message;
            }

            button.disabled = false;
        }
    );
});
