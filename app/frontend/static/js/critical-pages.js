// Adds and deletes a website's critical pages.
// Uses `failureReason` (helpers.js) and `confirmDeletion` (confirm-dialog.js).
// The dashboard's scripts share one scope, so they are loaded in order (see templates/index.html).

// Add a critical page to an existing website
const criticalPageForms =
    document.querySelectorAll(
        ".add-critical-page-form"
    );

criticalPageForms.forEach((form) => {

    form.addEventListener(
        "submit",
        async (event) => {

            event.preventDefault();

            const websiteId =
                form.dataset.websiteId;

            const input =
                form.querySelector(
                    ".new-critical-page-url"
                );

            const message =
                form.querySelector(
                    ".critical-page-message"
                );

            const pageUrl =
                input.value.trim();

            message.textContent =
                "Adding critical page...";

            try {

                const response =
                    await fetch(
                        "/scanner/initial_critical_page_scan",
                        {
                            method: "POST",
                            headers: {
                                "Content-Type":
                                    "application/json"
                            },
                            body: JSON.stringify({
                                url: pageUrl,
                                website_id:
                                    websiteId
                            })
                        }
                    );

                if (!response.ok) {
                    throw new Error(
                        await failureReason(response, "Unable to add critical page.")
                    );
                }

                window.location.reload();

            } catch (error) {

                message.textContent =
                    error.message;
            }
        }
    );
});


// Delete an existing critical page
const deleteCriticalPageButtons =
    document.querySelectorAll(
        ".delete-critical-page-button"
    );

deleteCriticalPageButtons.forEach(
    (button) => {

        button.addEventListener(
            "click",
            async () => {

                const pageId =
                    button.dataset.pageId;

                const confirmed =
                    await confirmDeletion({
                        title: "Delete this critical page?",
                        message: `${button.closest("li").querySelector("a").textContent.trim()} `
                            + "will no longer be monitored in detail.",
                    });

                if (!confirmed) {
                    return;
                }

                try {

                    const response =
                        await fetch(
                            `/critical_pages/${pageId}`,
                            {
                                method: "DELETE"
                            }
                        );

                    if (!response.ok) {
                        throw new Error(
                            "Unable to delete critical page."
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
