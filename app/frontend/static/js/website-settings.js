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

            const active =
                form.querySelector(
                    ".scan-active"
                ).checked;

            const delay =
                parseFloat(
                    form.querySelector(
                        ".scan-delay"
                    ).value
                );

            const concurrent =
                parseInt(
                    form.querySelector(
                        ".scan-concurrent"
                    ).value,
                    10
                );

            const daysBetweenScans =
                parseFloat(
                    form.querySelector(
                        ".days-between-scans"
                    ).value
                );

            const message =
                form.querySelector(
                    ".scan-settings-message"
                );

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
                            body: JSON.stringify({
                                active: active,
                                recommended_delay:
                                    delay,
                                recommended_concurrent:
                                    concurrent,
                                days_between_scans:
                                    daysBetweenScans
                            })
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
