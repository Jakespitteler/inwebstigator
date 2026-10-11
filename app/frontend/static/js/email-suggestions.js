(() => {
    const source = document.getElementById("saved-recipient-emails");
    if (!source) return;

    const addresses = Array.from(source.options, option => option.value);
    const selector = "input[data-saved-email-input]";
    const menu = document.createElement("div");
    menu.id = "saved-email-options";
    menu.className = "email-suggestions";
    menu.setAttribute("role", "listbox");
    menu.setAttribute("aria-label", "Saved email addresses");
    menu.hidden = true;
    document.body.appendChild(menu);

    let input = null;
    let matches = [];
    let selected = -1;

    function prepare(field) {
        field.setAttribute("role", "combobox");
        field.setAttribute("aria-autocomplete", "list");
        field.setAttribute("aria-haspopup", "listbox");
        field.setAttribute("aria-controls", menu.id);
        field.setAttribute("aria-expanded", "false");
    }

    function close() {
        menu.hidden = true;
        if (input) {
            input.setAttribute("aria-expanded", "false");
            input.removeAttribute("aria-activedescendant");
        }
        selected = -1;
    }

    function position() {
        if (!input || menu.hidden) return;
        const rect = input.getBoundingClientRect();
        if (!input.getClientRects().length || rect.bottom < 0 || rect.top > innerHeight) {
            close();
            return;
        }
        const below = innerHeight - rect.bottom - 12;
        const above = rect.top - 12;
        const openAbove = below < 140 && above > below;
        menu.style.width = `${Math.min(rect.width, innerWidth - 16)}px`;
        menu.style.left = `${Math.max(8, Math.min(rect.left, innerWidth - menu.offsetWidth - 8))}px`;
        menu.style.maxHeight = `${Math.max(40, Math.min(220, openAbove ? above : below))}px`;
        menu.style.top = `${openAbove ? rect.top - menu.offsetHeight - 4 : rect.bottom + 4}px`;
    }

    function render() {
        const query = input.value.trim().toLowerCase();
        matches = addresses.filter(address => address.toLowerCase().includes(query));
        selected = -1;
        input.removeAttribute("aria-activedescendant");
        menu.replaceChildren();
        matches.forEach((address, index) => {
            const option = document.createElement("div");
            option.id = `saved-email-option-${index}`;
            option.className = "email-suggestion-option";
            option.setAttribute("role", "option");
            option.setAttribute("aria-selected", "false");
            option.dataset.index = index;
            option.textContent = address;
            menu.appendChild(option);
        });
        if (!matches.length) {
            const empty = document.createElement("div");
            empty.className = "email-suggestions-empty";
            empty.setAttribute("role", "option");
            empty.setAttribute("aria-disabled", "true");
            empty.textContent = addresses.length
                ? "No matching saved emails. You can enter a new address."
                : "No saved emails yet. Enter a new address.";
            menu.appendChild(empty);
        }
        menu.hidden = false;
        input.setAttribute("aria-expanded", "true");
        position();
    }

    function open(field) {
        if (input !== field) close();
        input = field;
        prepare(input);
        render();
    }

    function choose(index) {
        if (!input || !matches[index]) return;
        input.value = matches[index];
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
        close();
    }

    document.querySelectorAll(selector).forEach(prepare);
    // Delegation includes email fields created by "Add another email".
    document.addEventListener("focusin", event => {
        if (event.target.matches(selector)) open(event.target);
        else close();
    });
    document.addEventListener("click", event => {
        if (event.target.matches(selector)) open(event.target);
    });
    document.addEventListener("input", event => {
        if (event.target.matches(selector)) open(event.target);
    });
    document.addEventListener("focusout", event => {
        if (event.target === input) close();
    });
    document.addEventListener("pointerdown", event => {
        if (!menu.contains(event.target) && event.target !== input) close();
    });
    menu.addEventListener("pointerdown", event => event.preventDefault());
    menu.addEventListener("click", event => {
        const option = event.target.closest("[data-index]");
        if (option) choose(Number(option.dataset.index));
    });
    // Capture selection keys before the form's Enter-to-next-step shortcut.
    document.addEventListener("keydown", event => {
        if (!event.target.matches(selector)) return;
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            event.stopPropagation();
            if (menu.hidden || input !== event.target) open(event.target);
            if (!matches.length) return;
            selected = selected < 0
                ? (event.key === "ArrowDown" ? 0 : matches.length - 1)
                : (selected + (event.key === "ArrowDown" ? 1 : -1) + matches.length) % matches.length;
            Array.from(menu.children).forEach((option, index) => {
                option.setAttribute("aria-selected", String(index === selected));
            });
            input.setAttribute("aria-activedescendant", menu.children[selected].id);
            menu.children[selected].scrollIntoView({ block: "nearest" });
        } else if (event.key === "Enter" && !menu.hidden && selected >= 0) {
            event.preventDefault();
            event.stopPropagation();
            choose(selected);
        } else if (event.key === "Escape" && !menu.hidden) {
            event.preventDefault();
            event.stopPropagation();
            close();
        } else if (event.key === "Tab") {
            close();
        }
    }, true);
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, true);
})();
