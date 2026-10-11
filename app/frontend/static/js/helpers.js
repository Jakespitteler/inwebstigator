// Small helpers used by the other scripts.
// The dashboard's scripts share one scope, so they are loaded in order (see templates/index.html).

// Returns the reason the server gave for a failed request, or the fallback if it gave no readable reason
async function failureReason(response, fallback) {
    const body = await response.json().catch(() => ({}));
    if (typeof body.detail === "string") {
        return body.detail;
    }
    // Our own validation errors start with "Value error, "; other validation messages are too technical
    const validationMessage = Array.isArray(body.detail) ? body.detail[0]?.msg ?? "" : "";
    return validationMessage.startsWith("Value error, ")
        ? validationMessage.slice("Value error, ".length)
        : fallback;
}

function filledValues(selector) {
    return Array.from(document.querySelectorAll(selector))
        .map((input) => input.value.trim())
        .filter((value) => value.length > 0);
}
