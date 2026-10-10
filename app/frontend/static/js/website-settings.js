// Deletes a website and saves its scan settings.
// Uses `confirmDeletion` (confirm-dialog.js).
// The dashboard's scripts share one scope, so they are loaded in order (see templates/index.html).

// Delete an existing website
const deleteWebsiteButtons =
    document.querySelectorAll(
        ".delete-website-button"
    );

deleteWebsiteButtons.forEach(
    (button) => {

        button.addEventListener(
            "click",
            async () => {

                const websiteId =
                    button.dataset.websiteId;

                const websiteName =
                    button.closest(".website-card").dataset.websiteName;

                const scanning =
                    !button.closest(".website-card").querySelector(
                        ".cancel-scan-button"
                    ).hidden;

                const confirmed =
                    await confirmDeletion({
                        title: `Delete ${websiteName}?`,
                        message: (scanning ? "Its scan will be cancelled. " : "")
                            + "It will stop being monitored, and its critical pages, saved page "
                            + "content and notification emails will be removed. This can't be undone.",
                        typeToConfirm: "CONFIRM",
                    });

                if (!confirmed) {
                    return;
                }

                try {

                    const response =
                        await fetch(
                            `/websites/${websiteId}`,
                            {
                                method: "DELETE"
                            }
                        );

                    if (!response.ok) {
                        throw new Error(
                            "Unable to delete website."
                        );
                    }

                    window.location.reload();

                } catch (error) {

                    window.alert(
                        error.message
                    );
                }
            }
        );
    }
);


// The settings changed since the page loaded, so a value the app changed since then (e.g. switching the website off,
// or slowing its crawl after it rate limited the crawler) is not saved back over with the page's old value
function changedScanSettings(form) {
    const settings = {};

    const active = form.querySelector(".scan-active");
    if (active.checked !== active.defaultChecked) {
        settings.active = active.checked;
    }

    const numberSettings = [
        ["recommended_delay", ".scan-delay", parseFloat],
        ["recommended_concurrent", ".scan-concurrent", (value) => parseInt(value, 10)],
        ["days_between_scans", ".days-between-scans", parseFloat],
    ];
    for (const [name, selector, parse] of numberSettings) {
        const input = form.querySelector(selector);
        if (parse(input.value) !== parse(input.defaultValue)) {
            settings[name] = parse(input.value);
        }
    }

    return settings;
}


// Update scan settings for an existing website
const scanSettingsForms =
    document.querySelectorAll(
        ".scan-settings-form"
    );

scanSettingsForms.forEach((form) => {

    form.addEventListener(
        "submit",
        async (event) => {

            event.preventDefault();

            const websiteId =
                form.dataset.websiteId;

            const settings =
                changedScanSettings(form);

            const message =
                form.querySelector(
                    ".scan-settings-message"
                );

            if (Object.keys(settings).length === 0) {
                message.textContent = "Nothing has changed.";
                return;
            }

            message.textContent =
                "Saving settings...";

            try {

                const websiteResponse =
                    await fetch(
                        `/websites/${websiteId}`,
                        {
                            method: "PATCH",
                            headers: {
                                "Content-Type":
                                    "application/json"
                            },
                            body: JSON.stringify(settings)
                        }
                    );

                if (!websiteResponse.ok) {
                    throw new Error(
                        "Unable to save website settings."
                    );
                }

                // Reload the page instead of showing a success message
                window.location.reload();

            } catch (error) {

                message.textContent =
                    error.message;
            }
        }
    );
});
