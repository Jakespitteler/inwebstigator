// Asks before deleting anything, with Cancel as the default.
// The dashboard's scripts share one scope, so they are loaded in order (see templates/index.html).

// Ask before deleting anything, with Cancel as the default.
// Resolves true only when the Delete button itself is clicked, or Enter is pressed after typing the text asked for.
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

// Typing the text is the confirmation, so Enter in its box deletes once the text matches, rather than pressing the
// first button (Cancel). Until it matches, Enter only says so. Enter anywhere else in the dialog still cancels.
confirmDialogInput.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.isComposing) {
        return;
    }
    event.preventDefault();
    if (confirmDialogButton.disabled) {
        confirmDialogError.hidden = false;
    } else {
        confirmDialog.close("confirm");
    }
});

// A click on the backdrop lands on the dialog element itself
confirmDialog.addEventListener("click", (event) => {
    if (event.target === confirmDialog) {
        confirmDialog.close("cancel");
    }
});
