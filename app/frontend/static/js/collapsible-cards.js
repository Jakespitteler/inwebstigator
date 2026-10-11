// Opens and closes the website, update and page cards, remembering which are open for this session.
// The dashboard's scripts share one scope, so they are loaded in order (see templates/index.html).

// Website cards and update cards (and the page cards inside them) start collapsed; clicking anywhere on one opens it.
// Open cards are remembered for this session, as adding an email or page reloads the dashboard.
const EXPANDED_CARDS_KEY = "inwebstigator-expanded-cards";

function readExpandedCards() {
    try {
        return new Set(JSON.parse(sessionStorage.getItem(EXPANDED_CARDS_KEY)) || []);
    } catch (error) {
        return new Set();  // Storage unavailable or unreadable, so start with every card closed
    }
}

const expandedCards = readExpandedCards();

function setCardExpanded(card, expanded) {
    const toggle = card.querySelector(".card-toggle");

    document.getElementById(toggle.getAttribute("aria-controls")).hidden = !expanded;
    card.classList.toggle("expanded", expanded);
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.setAttribute("aria-label", `${expanded ? "Hide" : "Show"} details for ${card.dataset.cardName}`);

    if (expanded) {
        expandedCards.add(card.dataset.cardKey);
    } else {
        expandedCards.delete(card.dataset.cardKey);
    }

    try {
        sessionStorage.setItem(EXPANDED_CARDS_KEY, JSON.stringify([...expandedCards]));
    } catch (error) {
        // Not remembering open cards is fine
    }
}

document.querySelectorAll("[data-collapsible]").forEach((card) => {
    if (expandedCards.has(card.dataset.cardKey)) {
        setCardExpanded(card, true);
    }

    card.addEventListener("click", (event) => {
        // A click inside a page's card belongs to that card, not the website card around it
        if (event.target.closest("[data-collapsible]") !== card) {
            return;
        }

        const expanded = card.classList.contains("expanded");

        if (event.target.closest(".card-toggle")) {
            setCardExpanded(card, !expanded);
            return;
        }

        // Links, buttons and form fields keep doing their own job, and selecting text isn't a click
        if (event.target.closest("a, button, input, select, textarea, label, summary, form")
                || window.getSelection().toString()) {
            return;
        }

        if (!expanded) {
            setCardExpanded(card, true);
        } else if (event.target.closest(".card-header")) {
            setCardExpanded(card, false);
        }
    });
});
