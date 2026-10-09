// Runs and cancels scans, and says that closing the window won't stop them.
// Uses `setCardExpanded` (collapsible-cards.js).
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
                    // 409 means it was already queued or was cancelled, which the server explains
                    throw new Error(
                        response.status === 409
                            ? (await response.json()).detail
                            : "Unable to complete scan."
                    );
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

if (runAllScansButton) {
    runAllScansButton.addEventListener("click", async () => {
        const message = document.getElementById("run-all-scans-message");

        runAllScansButton.disabled = true;
        message.classList.add("is-busy");
        message.textContent = "Scanning every website. Large websites can take several minutes...";

        try {
            const response = await fetch("/scanner/run_all", { method: "POST" });
            if (!response.ok) {
                throw new Error("Unable to complete the scans.");
            }
        } catch (error) {
            message.classList.remove("is-busy");
            message.textContent = error.message;
            runAllScansButton.disabled = false;
            return;
        }

        window.location.reload();
    });
}

// While any scan runs (a spinner is showing), say that closing the window won't stop it
const backgroundScanNote = document.getElementById("background-scan-note");

function updateBackgroundScanNote() {
    const addingWebsite = document.getElementById("add-website-progress");
    const scanning =
        Boolean(document.querySelector(".is-busy"))
        || Boolean(addingWebsite && !addingWebsite.hidden);

    // Only touch it when it changes, as the observer below would otherwise be woken up forever
    if (backgroundScanNote.hidden === scanning) {
        backgroundScanNote.hidden = !scanning;
    }
}

if (backgroundScanNote) {
    new MutationObserver(updateBackgroundScanNote).observe(
        document.querySelector(".websites-section"),
        { subtree: true, attributes: true, attributeFilter: ["class", "hidden"] }
    );
    updateBackgroundScanNote();
}

// Cancel a website's scan, whether it is still queued or already running
const cancelScanButtons =
    document.querySelectorAll(
        ".cancel-scan-button"
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
