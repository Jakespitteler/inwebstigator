// Asks before deleting anything, with Cancel as the default.
// The dashboard's scripts share one scope, so they are loaded in order (see templates/index.html).

// Ask before deleting anything, with Cancel as the default.
// Resolves true only when the Delete button itself is clicked.
const confirmDialog = document.getElementById("confirm-dialog");
const confirmDialogInput = document.getElementById("confirm-dialog-input");
const confirmDialogButton = document.getElementById("confirm-dialog-confirm");
const confirmDialogExpected = document.getElementById("confirm-dialog-expected");
const confirmDialogError = document.getElementById("confirm-dialog-error");

function confirmDeletion({ title, message, confirmLabel = "Delete", typeToConfirm = null }) {
    document.getElementById("confirm-dialog-title").textContent = title;
    document.getElementById("confirm-dialog-message").textContent = message;
    document.getElementById("confirm-dialog-type-row").hidden = !typeToConfirm;
    confirmDialogExpected.textContent = typeToConfirm || "";
    confirmDialogInput.value = "";
    confirmDialogError.hidden = true;
    confirmDialogButton.textContent = confirmLabel;
    confirmDialogButton.disabled = Boolean(typeToConfirm);

    // Closing any other way (Escape, clicking outside) counts as Cancel
    confirmDialog.returnValue = "cancel";

    return new Promise((resolve) => {
        confirmDialog.addEventListener(
            "close",
            () => resolve(confirmDialog.returnValue === "confirm"),
            { once: true }
        );
        confirmDialog.showModal();
        (typeToConfirm ? confirmDialogInput : document.getElementById("confirm-dialog-cancel")).focus();
    });
}

confirmDialogInput.addEventListener("input", () => {
    const enteredText = confirmDialogInput.value.trim();
    const matches =
        enteredText === confirmDialogExpected.textContent;

    confirmDialogButton.disabled = !matches;
    confirmDialogError.hidden =
        enteredText === "" || matches;
});

// A click on the backdrop lands on the dialog element itself
confirmDialog.addEventListener("click", (event) => {
    if (event.target === confirmDialog) {
        confirmDialog.close("cancel");
    }
});
