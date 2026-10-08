// Moves through the steps of the add website form, and adds more critical page and email fields.
// Uses `filledValues` (helpers.js).
// The dashboard's scripts share one scope, so they are loaded in order (see templates/index.html).

// Step through the add website wizard
const guideSteps = Array.from(document.querySelectorAll(".guide-step"));
const guideDots = Array.from(document.querySelectorAll(".guide-dot"));
const guideBack = document.getElementById("guide-back");
const guideNext = document.getElementById("guide-next");
const guideSubmit = document.getElementById("guide-submit");
let currentGuideStep = 1;


// Returns true when every field on the step is valid, otherwise shows the browser's message.
function guideStepIsValid(step) {
    const inputs = guideSteps[step - 1].querySelectorAll("input");
    return Array.from(inputs).every((input) => input.reportValidity());
}

function renderGuideReview() {
    const review = document.getElementById("guide-review");
    const rows = [
        ["Website", [document.getElementById("website-url").value.trim()]],
        ["Critical pages", filledValues(".critical-page-input")],
        ["Notification emails", filledValues(".recipient-email-input")],
        ["Days between scans", [document.getElementById("new-website-days").value]],
        ["Request delay", [`${document.getElementById("new-website-delay").value} seconds`]],
        ["Concurrent requests", [document.getElementById("new-website-concurrent").value]],
    ];

    review.replaceChildren();
    rows.forEach(([label, values]) => {
        const term = document.createElement("dt");
        term.textContent = label;
        const detail = document.createElement("dd");
        detail.textContent = values.length ? values.join(", ") : "None";
        review.append(term, detail);
    });
}

function showGuideStep(step) {
    // Moving forward must not skip past a step with invalid input.
    for (let previous = currentGuideStep; previous < step; previous++) {
        if (!guideStepIsValid(previous)) {
            step = previous;
            break;
        }
    }

    currentGuideStep = step;
    guideSteps.forEach((item) => {
        item.hidden = Number(item.dataset.step) !== step;
    });
    guideDots.forEach((dot) => {
        const current = Number(dot.dataset.step) === step;
        dot.classList.toggle("active", current);
        if (current) {
            dot.setAttribute("aria-current", "step");
        } else {
            dot.removeAttribute("aria-current");
        }
    });

    const lastStep = step === guideSteps.length;
    guideBack.disabled = step === 1;
    guideNext.hidden = lastStep;
    guideSubmit.hidden = !lastStep;

    if (lastStep) {
        renderGuideReview();
    }
}

if (guideSteps.length) {
    guideDots.forEach((dot) => {
        dot.addEventListener("click", () => showGuideStep(Number(dot.dataset.step)));
    });
    guideBack.addEventListener("click", () => showGuideStep(currentGuideStep - 1));
    guideNext.addEventListener("click", () => showGuideStep(currentGuideStep + 1));

    // Enter in a field moves to the next step rather than submitting early.
    document.getElementById("add-website-form").addEventListener("keydown", (event) => {
        if (event.key === "Enter" && event.target.tagName === "INPUT"
                && currentGuideStep < guideSteps.length) {
            event.preventDefault();
            showGuideStep(currentGuideStep + 1);
        }
    });

    showGuideStep(1);
}

// Once websites exist the wizard starts collapsed behind the "+ Add website" button
const addWebsiteToggle = document.getElementById("add-website-toggle");

if (addWebsiteToggle) {
    addWebsiteToggle.addEventListener("click", () => {
        const form = document.getElementById("add-website-form");
        const opening = form.hidden;

        form.hidden = !opening;
        addWebsiteToggle.setAttribute("aria-expanded", String(opening));
        addWebsiteToggle.textContent = opening ? "Close" : "+ Add website";

        if (opening) {
            showGuideStep(1);
            document.getElementById("website-url").focus();
        }
    });
}


// Add extra critical-page fields when creating a website
const addCriticalPageButton =
    document.getElementById(
        "add-critical-page-input"
    );

if (addCriticalPageButton) {

    addCriticalPageButton.addEventListener(
        "click",
        () => {

            const input =
                document.createElement("input");

            input.type = "text";
            input.className =
                "critical-page-input";
            input.placeholder =
                "https://example.com/critical-page";

            document
                .getElementById(
                    "critical-page-inputs"
                )
                .appendChild(input);
        }
    );
}


// Add extra recipient email fields when creating a website
const addRecipientEmailButton =
    document.getElementById(
        "add-recipient-email-input"
    );

if (addRecipientEmailButton) {

    addRecipientEmailButton.addEventListener(
        "click",
        () => {

            const input =
                document.createElement("input");

            input.type = "email";
            input.className =
                "recipient-email-input";
            input.placeholder =
                "name@example.com";

            document
                .getElementById(
                    "recipient-email-inputs"
                )
                .appendChild(input);
        }
    );
}
