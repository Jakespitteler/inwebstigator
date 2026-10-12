// Tag management and combined tag/search filtering. Card nodes stay in place so forms and scans keep working.
// Uses failureReason and confirmDeletion from the scripts loaded before this one.
(() => {
    const container = document.getElementById("monitored-website-list");
    if (!container) return;

    const cards = Array.from(container.querySelectorAll(".website-card"));
    const filters = document.getElementById("website-tag-filters");
    const search = document.getElementById("website-search");
    const count = document.getElementById("website-result-count");
    const noMatches = document.getElementById("no-matching-websites");
    const message = document.getElementById("tag-message");
    const manageButton = document.getElementById("manage-tags-button");
    const dialog = document.getElementById("tag-dialog");
    const list = document.getElementById("tag-dialog-list");
    const form = document.getElementById("tag-form");
    const nameInput = document.getElementById("tag-name");
    const formMessage = document.getElementById("tag-form-message");
    const closeButton = document.getElementById("close-tag-dialog");
    const cancelEdit = document.getElementById("cancel-tag-edit");
    const saveButton = document.getElementById("save-tag-button");
    const tags = new Map(JSON.parse(document.getElementById("website-tag-data").textContent)
        .map(tag => [tag.id, tag]));
    const storageKey = "inwebstigator-website-filters";
    let selectedTag = "";
    let activeCard = null;
    let editingTag = null;
    let busy = false;
    let dialogOpener = null;

    try {
        const saved = JSON.parse(sessionStorage.getItem(storageKey) || "{}");
        if (tags.has(saved.tag)) selectedTag = saved.tag;
        if (typeof saved.search === "string") search.value = saved.search;
    } catch { /* Search and tags still work when storage is unavailable. */ }

    const sortedTags = () => [...tags.values()].sort((a, b) => a.name.localeCompare(b.name));
    const cardTags = card => JSON.parse(card.dataset.tagIds);

    function announce(text, error = false, inDialog = false) {
        const target = inDialog ? formMessage : message;
        target.textContent = text;
        target.classList.toggle("is-error", error);
    }

    function setBusy(value) {
        busy = value;
        manageButton.disabled = value;
        dialog.querySelectorAll("button, input").forEach(control => { control.disabled = value; });
        container.querySelectorAll(".website-tag-row button").forEach(control => { control.disabled = value; });
    }

    async function request(path, method = "GET", data) {
        const response = await fetch(path, {
            method,
            ...(data === undefined ? {} : {
                headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
            }),
        });
        if (!response.ok) throw new Error(await failureReason(response, "Unable to save tags. Please try again."));
        return response.status === 204 ? null : response.json();
    }

    function filterWebsites() {
        if (selectedTag && !tags.has(selectedTag)) selectedTag = "";
        const terms = search.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
        let visible = 0;
        cards.forEach(card => {
            const ids = cardTags(card);
            const text = [card.dataset.websiteName, card.dataset.websiteUrl,
                ...ids.map(id => tags.get(id)?.name || "")].join(" ").toLocaleLowerCase();
            card.hidden = Boolean(selectedTag && !ids.includes(selectedTag))
                || !terms.every(term => text.includes(term));
            if (!card.hidden) visible++;
        });
        filters.querySelectorAll(".tag-filter").forEach(button => {
            const selected = button.dataset.tagId === selectedTag;
            button.classList.toggle("active", selected);
            button.setAttribute("aria-pressed", String(selected));
        });
        count.textContent = `${visible} of ${cards.length} ${cards.length === 1 ? "website" : "websites"}`;
        noMatches.hidden = visible > 0 || cards.length === 0;
        try { sessionStorage.setItem(storageKey, JSON.stringify({ tag: selectedTag, search: search.value })); }
        catch { /* Remembering filters is optional. */ }
    }

    function renderFilters() {
        filters.replaceChildren();
        [{ id: "", name: "All" }, ...sortedTags()].forEach(tag => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "tag-filter";
            button.dataset.tagId = tag.id;
            button.textContent = tag.name;
            button.title = tag.name;
            filters.append(button);
        });
        filters.append(manageButton);
    }

    function renderCardTags(card) {
        const chips = card.querySelector(".website-tag-chips");
        chips.replaceChildren();
        const assigned = new Set(cardTags(card));
        sortedTags().filter(tag => assigned.has(tag.id)).forEach(tag => {
            const chip = document.createElement("span");
            chip.className = "website-tag";
            chip.title = tag.name;
            const label = document.createElement("span");
            label.className = "website-tag-name";
            label.textContent = tag.name;
            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "remove-website-tag";
            remove.dataset.tagId = tag.id;
            remove.textContent = "×";
            remove.setAttribute("aria-label", `Remove ${tag.name} tag from ${card.dataset.websiteName}`);
            chip.append(label, remove);
            chips.append(chip);
        });
    }

    function actionButton(label, action, tag) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `tag-text-button tag-${action}`;
        button.dataset.action = action;
        button.dataset.tagId = tag.id;
        button.textContent = label;
        button.setAttribute("aria-label", `${label} ${tag.name} tag`);
        return button;
    }

    function renderDialog() {
        const assigned = new Set(activeCard ? cardTags(activeCard) : []);
        list.replaceChildren();
        sortedTags().forEach(tag => {
            const row = document.createElement("div");
            row.className = "tag-dialog-row";
            const label = document.createElement(activeCard ? "label" : "span");
            label.className = activeCard ? "tag-dialog-choice" : "tag-dialog-row-name";
            if (activeCard) {
                const checkbox = document.createElement("input");
                checkbox.type = "checkbox";
                checkbox.dataset.tagId = tag.id;
                checkbox.checked = assigned.has(tag.id);
                label.append(checkbox);
            }
            const text = document.createElement("span");
            text.className = "tag-dialog-row-name";
            text.textContent = tag.name;
            label.append(text);
            row.append(label);
            if (!activeCard) {
                const actions = document.createElement("div");
                actions.className = "tag-dialog-actions";
                actions.append(actionButton("Rename", "rename", tag), actionButton("Delete", "delete", tag));
                row.append(actions);
            }
            list.append(row);
        });
        document.getElementById("tag-dialog-empty").hidden = tags.size > 0;
    }

    function render() {
        renderFilters();
        cards.forEach(renderCardTags);
        if (dialog.open) renderDialog();
        filterWebsites();
    }

    function resetEditor() {
        editingTag = null;
        nameInput.value = "";
        document.getElementById("tag-name-label").textContent = "New tag";
        saveButton.textContent = "Add";
        cancelEdit.hidden = true;
    }

    async function openDialog(card, opener) {
        if (busy) return;
        activeCard = card;
        dialogOpener = opener;
        resetEditor();
        announce("", false, true);
        document.getElementById("tag-dialog-title").textContent = card ? `Tags for ${card.dataset.websiteName}` : "Tags";
        document.getElementById("tag-dialog-description").textContent = card
            ? "Select tags to add or remove them for this website. Changes save automatically."
            : "Create, rename or delete tags.";
        renderDialog();
        dialog.showModal();
        setBusy(true);
        try {
            const [savedTags, assigned] = await Promise.all([
                request("/tags"), card ? request(`/websites/${card.dataset.websiteId}/tags`) : Promise.resolve(null),
            ]);
            tags.clear();
            savedTags.forEach(tag => tags.set(tag.id, tag));
            if (card) card.dataset.tagIds = JSON.stringify(assigned.map(tag => tag.id));
            render();
        } catch (error) {
            announce(error.message, true, true);
        } finally {
            setBusy(false);
            (list.querySelector("input") || nameInput).focus();
        }
    }

    async function assignTag(card, tagId, assigned) {
        const saved = await request(`/websites/${card.dataset.websiteId}/tags/${tagId}`, assigned ? "POST" : "DELETE");
        saved.forEach(tag => tags.set(tag.id, tag));
        card.dataset.tagIds = JSON.stringify(saved.map(tag => tag.id));
    }

    filters.addEventListener("click", event => {
        const button = event.target.closest(".tag-filter");
        if (!button) return;
        selectedTag = button.dataset.tagId;
        filterWebsites();
    });
    search.addEventListener("input", filterWebsites);
    manageButton.addEventListener("click", () => openDialog(null, manageButton));
    closeButton.addEventListener("click", () => dialog.close());
    dialog.addEventListener("cancel", event => { if (busy) event.preventDefault(); });
    dialog.addEventListener("close", () => {
        activeCard = null;
        // Removing the last matching tag can hide its card. Return focus to the active filter in that case.
        const fallback = filters.querySelector('[aria-pressed="true"]');
        (dialogOpener?.closest(".website-card")?.hidden ? fallback : dialogOpener)?.focus();
    });
    cancelEdit.addEventListener("click", () => { resetEditor(); nameInput.focus(); });

    container.addEventListener("click", async event => {
        const add = event.target.closest(".add-website-tags");
        if (add) { openDialog(add.closest(".website-card"), add); return; }
        const remove = event.target.closest(".remove-website-tag");
        if (!remove || busy) return;
        const card = remove.closest(".website-card");
        const tag = tags.get(remove.dataset.tagId);
        setBusy(true);
        try {
            await assignTag(card, tag.id, false);
            render();
            announce(`${tag.name} removed from ${card.dataset.websiteName}.`);
        } catch (error) {
            announce(error.message, true);
        } finally {
            setBusy(false);
            (card.hidden ? filters.querySelector('[aria-pressed="true"]') : card.querySelector(".add-website-tags")).focus();
        }
    });

    list.addEventListener("change", async event => {
        const checkbox = event.target.closest('input[type="checkbox"]');
        if (!checkbox || !activeCard || busy) return;
        const tagId = checkbox.dataset.tagId;
        const assigned = checkbox.checked;
        setBusy(true);
        try {
            await assignTag(activeCard, tagId, assigned);
            announce(`${tags.get(tagId).name} ${assigned ? "added" : "removed"}.`, false, true);
        } catch (error) {
            announce(error.message, true, true);
        } finally {
            render();
            setBusy(false);
            list.querySelector(`input[data-tag-id="${tagId}"]`)?.focus();
        }
    });

    list.addEventListener("click", async event => {
        const button = event.target.closest("button[data-action]");
        if (!button || busy || activeCard) return;
        const tag = tags.get(button.dataset.tagId);
        if (button.dataset.action === "rename") {
            editingTag = tag.id;
            nameInput.value = tag.name;
            document.getElementById("tag-name-label").textContent = "Rename tag";
            saveButton.textContent = "Save";
            cancelEdit.hidden = false;
            announce("", false, true);
            nameInput.focus();
            return;
        }
        const confirmed = await confirmDeletion({
            title: `Delete ${tag.name}?`,
            message: "This tag will be removed from every website. Websites, scan history and settings will be kept.",
            confirmLabel: "Delete tag",
        });
        if (!confirmed || busy || activeCard) return;
        setBusy(true);
        try {
            await request(`/tags/${tag.id}`, "DELETE");
            tags.delete(tag.id);
            cards.forEach(card => {
                card.dataset.tagIds = JSON.stringify(cardTags(card).filter(id => id !== tag.id));
            });
            if (editingTag === tag.id) resetEditor();
            render();
            announce(`${tag.name} deleted.`, false, true);
        } catch (error) {
            announce(error.message, true, true);
        } finally {
            setBusy(false);
            nameInput.focus();
        }
    });

    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (busy || (activeCard && editingTag)) return;
        setBusy(true);
        let created = null;
        try {
            const renamed = Boolean(editingTag);
            const tag = await request(editingTag ? `/tags/${editingTag}` : "/tags", editingTag ? "PATCH" : "POST", {
                name: nameInput.value,
            });
            tags.set(tag.id, tag);
            if (!renamed) created = tag;
            if (!renamed && activeCard) await assignTag(activeCard, tag.id, true);
            resetEditor();
            announce(`${tag.name} ${renamed ? "renamed" : activeCard ? "created and added" : "created"}.`, false, true);
        } catch (error) {
            announce(created ? `Tag created, but could not be added. Select it above to try again. ${error.message}`
                : error.message, true, true);
        } finally {
            render();
            setBusy(false);
            nameInput.focus();
        }
    });

    filterWebsites();
})();
