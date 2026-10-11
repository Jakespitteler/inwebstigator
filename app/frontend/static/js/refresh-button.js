// The Refresh button in the page header.
// The dashboard's scripts share one scope, so they are loaded in order (see templates/index.html).

// Refresh button: reload the dashboard to show the latest results, e.g. once a scan running in the
// background has finished. Scans carry on during the reload, and the selected tab and open cards are kept.
const reloadPageButton = document.getElementById("reloadPageBtn");

if (reloadPageButton) {
    reloadPageButton.addEventListener("click", () => {
        reloadPageButton.disabled = true;
        reloadPageButton.classList.add("is-reloading");
        window.location.reload();
    });
}
