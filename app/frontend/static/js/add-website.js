// Adds a website, showing a line for each website being added while its first scan runs.
// Uses `failureReason` and `filledValues` (helpers.js) and the wizard (add-website-wizard.js).
// The dashboard's scripts share one scope, so they are loaded in order (see templates/index.html).

// Each website being added gets its own line while its first scan runs, so the form stays free for
// adding another website meanwhile. The server scans them one at a time, in the order they were added.
const addWebsiteForm = document.getElementById("add-website-form");
const addWebsiteProgressList = document.getElementById("add-website-progress-list");
const addWebsiteProgressTemplate = document.getElementById("add-website-progress-template");

// The URLs of the websites whose first scan is running, so the same website isn't added twice at once
const websitesBeingAdded = new Set();

// The name a website is shown by, e.g. "https://www.uwa.edu.au/" -> "uwa.edu.au"
function displayName(url) {
    try {
        return new URL(/^https?:\/\//i.test(url) ? url : `https://${url}`).hostname.replace(/^www\./, "");
    } catch (error) {
        return url;
    }
}

// The URL as the server saves it, e.g. "example.com" -> "https://example.com", so it can be cancelled by it
function withScheme(url) {
    return /^[a-z][a-z\d+.-]*:\/\//i.test(url) ? url : `https://${url}`;
}

// Everything entered in the add website form, as typed, so it can be put back to try again
function readAddWebsiteForm() {
    return {
        url: document.getElementById("website-url").value.trim(),
        criticalPages: filledValues(".critical-page-input"),
        recipientEmails: filledValues(".recipient-email-input"),
        daysBetweenScans: document.getElementById("new-website-days").value,
        delay: document.getElementById("new-website-delay").value,
        concurrent: document.getElementById("new-website-concurrent").value,
    };
}

function addWebsiteFormIsEmpty() {
    const { url, criticalPages, recipientEmails } = readAddWebsiteForm();
    return !url && !criticalPages.length && !recipientEmails.length;
}

// Puts the add website form back to how it started, ready for the next website
function resetAddWebsiteForm() {
    addWebsiteForm.reset();
    // Keep the first field of each list, removing any added with "+ Add another"
    document.querySelectorAll(
        "#critical-page-inputs input:not(:first-child), #recipient-email-inputs input:not(:first-child)"
    ).forEach((input) => input.remove());
    document.getElementById("website-form-message").textContent = "";
    showGuideStep(1);
}

// Fills a list of fields with values, adding fields with its "+ Add another" button as needed
function fillInputs(selector, addButton, values) {
    while (document.querySelectorAll(selector).length < values.length) {
        addButton.click();
    }
    document.querySelectorAll(selector).forEach((input, index) => {
        input.value = values[index] ?? "";
    });
}

// Puts a website that could not be added back in the form, at the review step, so it can be tried again
function fillAddWebsiteForm(values) {
    resetAddWebsiteForm();
    document.getElementById("website-url").value = values.url;
    fillInputs(".critical-page-input", addCriticalPageButton, values.criticalPages);
    fillInputs(".recipient-email-input", addRecipientEmailButton, values.recipientEmails);
    document.getElementById("new-website-days").value = values.daysBetweenScans;
    document.getElementById("new-website-delay").value = values.delay;
    document.getElementById("new-website-concurrent").value = values.concurrent;

    if (addWebsiteForm.hidden && addWebsiteToggle) {
        addWebsiteToggle.click();  // Opens the form
    }
    showGuideStep(guideSteps.length);
}

// Reloads the dashboard to show the websites just added, but only once nothing would be lost by it:
// no website still being added, no failure still to read, and nothing typed into the form
function reloadWhenIdle() {
    const websiteAdded = addWebsiteProgressList.querySelector("[data-state='added']");
    const failureShown = addWebsiteProgressList.querySelector("[data-state='failed']");
    if (websiteAdded && !failureShown && websitesBeingAdded.size === 0 && addWebsiteFormIsEmpty()) {
        window.location.reload();
    }
}

// Cancels a website's first scan, which cancels adding the website
async function cancelAdding(adding) {
    const status = adding.line.querySelector(".add-website-progress-status");
    const cancelButton = adding.line.querySelector(".add-website-cancel");
    const formData = new FormData();
    formData.append("url", adding.url);

    // Set before asking, as the add request can finish before the cancel request does
    adding.cancelled = true;
    cancelButton.disabled = true;
    status.textContent = "Cancelling scan…";

    try {
        const response = await fetch("/scanner/cancel", { method: "POST", body: formData });
        if (!response.ok) {
            throw new Error("Unable to cancel scan.");
        }
        if (!(await response.json())) {
            throw new Error("The scan could not be cancelled, as it had already finished.");
        }
    } catch (error) {
        adding.cancelled = false;
        cancelButton.disabled = false;
        status.textContent = error.message;
    }
}

// Adds a "Scanning ..." line for a website being added, with buttons for each way it can end
function showAddingLine(adding, values) {
    const line = addWebsiteProgressTemplate.content.firstElementChild.cloneNode(true);
    line.querySelector(".add-website-progress-status").textContent = `Scanning ${displayName(adding.url)}…`;
    line.querySelector(".add-website-cancel").addEventListener("click", () => cancelAdding(adding));
    line.querySelector(".add-website-retry").addEventListener("click", () => {
        line.remove();
        fillAddWebsiteForm(values);
    });
    line.querySelector(".add-website-dismiss").addEventListener("click", () => {
        line.remove();
        reloadWhenIdle();
    });
    line.querySelector(".add-website-refresh").addEventListener("click", () => window.location.reload());
    addWebsiteProgressList.append(line);
    return line;
}

// Shows on a website's line how adding it ended: "added", or "failed" with the reason
function finishAddingLine(line, state, statusText, detailText) {
    line.dataset.state = state;
    line.querySelector(".spinner").hidden = true;
    line.querySelector(".add-website-progress-status").textContent = statusText;
    line.querySelector(".add-website-progress-detail").textContent = detailText;
    line.querySelector(".add-website-cancel").hidden = true;
    line.querySelector(".add-website-retry").hidden = state !== "failed";
    line.querySelector(".add-website-dismiss").hidden = state !== "failed";
    line.querySelector(".add-website-refresh").hidden = state !== "added";
}


// Add a new website. The form is cleared straight away, so another website can be added while this one's
// first scan runs, and how adding this one went is shown on its own line.
if (addWebsiteForm) {

    addWebsiteForm.addEventListener(
        "submit",
        async (event) => {

            event.preventDefault();

            const values = readAddWebsiteForm();
            const websiteUrl = withScheme(values.url);
            const message = document.getElementById("website-form-message");

            // The server would refuse it too, but only after emailing its recipients again
            if (websitesBeingAdded.has(websiteUrl)) {
                message.textContent = `${displayName(websiteUrl)} is already being added.`;
                return;
            }

            const adding = { url: websiteUrl, cancelled: false };
            adding.line = showAddingLine(adding, values);
            websitesBeingAdded.add(websiteUrl);
            resetAddWebsiteForm();

            const intro = document.querySelector(".guide-intro");
            if (intro) {
                intro.hidden = true;
            }

            try {

                const response =
                    await fetch(
                        "/scanner/initial_scan",
                        {
                            method: "POST",
                            headers: {
                                "Content-Type":
                                    "application/json"
                            },
                            body: JSON.stringify({
                                url: websiteUrl,
                                critical_pages:
                                    values.criticalPages,
                                recipient_emails:
                                    values.recipientEmails,
                                recommended_delay: parseFloat(values.delay),
                                recommended_concurrent: parseInt(values.concurrent, 10),
                                days_between_scans: parseFloat(values.daysBetweenScans)
                            })
                        }
                    );

                if (!response.ok) {
                    // 409 means the first scan was cancelled, or the website is already being scanned
                    throw new Error(
                        response.status !== 409
                            ? await failureReason(response, "Unable to add website.")
                            : adding.cancelled
                                ? "Scan cancelled."
                                : (await response.json()).detail
                    );
                }

                finishAddingLine(
                    adding.line, "added", `Added ${displayName(websiteUrl)}.`, "Refresh to see it on the dashboard."
                );

            } catch (error) {

                finishAddingLine(adding.line, "failed", error.message, `${displayName(websiteUrl)} was not added.`);

            } finally {
                websitesBeingAdded.delete(websiteUrl);
                reloadWhenIdle();
            }
        }
    );
}
