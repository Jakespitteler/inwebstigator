// Adds and removes the emails a website's reports are sent to.
// Uses `failureReason` (helpers.js) and `confirmDeletion` (confirm-dialog.js).
// The dashboard's scripts share one scope, so they are loaded in order (see templates/index.html).

// Add a notification email to an existing website
const recipientForms =
    document.querySelectorAll(
        ".add-recipient-form"
    );

recipientForms.forEach((form) => {

    form.addEventListener(
        "submit",
        async (event) => {

            event.preventDefault();

            const websiteId =
                form.dataset.websiteId;

            const input =
                form.querySelector(
                    ".new-recipient-email"
                );

            const message =
                form.querySelector(
                    ".recipient-message"
                );

            const email =
                input.value.trim();

            message.textContent =
                "Sending a confirmation email. This can take up to a minute...";

            try {

                const response =
                    await fetch(
                        `/websites/${websiteId}`,
                        {
                            method: "PATCH",
                            headers: {
                                "Content-Type":
                                    "application/json"
                            },
                            body: JSON.stringify({
                                add_recipient_emails: [
                                    email
                                ]
                            })
                        }
                    );

                if (!response.ok) {
                    throw new Error(
                        await failureReason(response, "Unable to add notification email.")
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


// Remove a notification email from an existing website
const deleteRecipientButtons =
    document.querySelectorAll(
        ".delete-recipient-button"
    );

deleteRecipientButtons.forEach(
    (button) => {

        button.addEventListener(
            "click",
            async () => {

                const websiteId =
                    button.dataset.websiteId;

                const email =
                    button.dataset.recipientEmail;

                const confirmed =
                    await confirmDeletion({
                        title: `Remove ${email}?`,
                        message: "They will stop receiving reports for "
                            + `${button.closest(".website-card").dataset.websiteName}.`,
                        confirmLabel: "Remove",
                    });

                if (!confirmed) {
                    return;
                }

                try {

                    const response =
                        await fetch(
                            `/websites/${websiteId}`,
                            {
                                method: "PATCH",
                                headers: {
                                    "Content-Type":
                                        "application/json"
                            },
                            body: JSON.stringify({
                                remove_recipient_emails: [
                                    email
                                ]
                            })
                        }
                    );

                    if (!response.ok) {
                        throw new Error(
                            "Unable to remove notification email."
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
