// The Websites / Updates tabs, which keep the selected tab when the dashboard reloads.
// The dashboard's scripts share one scope, so they are loaded in order (see templates/index.html).

// Keep the selected section when forms and scans refresh the dashboard.
const dashboardTabs = Array.from(document.querySelectorAll('[role="tab"]'));

function selectDashboardTab(tab) {
    dashboardTabs.forEach((item) => {
        const selected = item === tab;
        item.classList.toggle("active", selected);
        item.setAttribute("aria-selected", String(selected));
        item.tabIndex = selected ? 0 : -1;
        document.getElementById(item.getAttribute("aria-controls")).hidden = !selected;
    });
}

function restoreDashboardTab() {
    selectDashboardTab(document.getElementById(
        window.location.hash === "#updates" ? "updates-tab" : "websites-tab"
    ));
}

dashboardTabs.forEach((tab, index) => {
    tab.addEventListener("click", () => {
        selectDashboardTab(tab);
        window.location.hash = tab.id === "websites-tab" ? "websites" : "updates";
    });
    tab.addEventListener("keydown", (event) => {
        let nextTab;
        if (event.key === "ArrowRight") nextTab = dashboardTabs[(index + 1) % dashboardTabs.length];
        if (event.key === "ArrowLeft") {
            nextTab = dashboardTabs[(index + dashboardTabs.length - 1) % dashboardTabs.length];
        }
        if (event.key === "Home") nextTab = dashboardTabs[0];
        if (event.key === "End") nextTab = dashboardTabs[dashboardTabs.length - 1];
        if (nextTab) {
            event.preventDefault();
            nextTab.focus();
            nextTab.click();
        }
    });
});

window.addEventListener("hashchange", restoreDashboardTab);
restoreDashboardTab();
