// Light / dark mode, shared by every page.
// Loaded in <head> so the saved theme is applied before the first paint, and the page never flashes the wrong one.
(function () {
    var THEME_KEY = "inwebstigator-theme";
    var darkScheme = window.matchMedia("(prefers-color-scheme: dark)");

    function savedTheme() {
        try {
            return localStorage.getItem(THEME_KEY);
        } catch (error) {
            // Storage can be unavailable (e.g. private mode); fall back to the OS setting
            return null;
        }
    }

    function applyTheme(theme) {
        document.documentElement.setAttribute("data-theme", theme);
    }

    applyTheme(savedTheme() || (darkScheme.matches ? "dark" : "light"));

    // Follow the OS setting until the user picks a theme themselves
    darkScheme.addEventListener("change", function (event) {
        if (!savedTheme()) {
            applyTheme(event.matches ? "dark" : "light");
        }
    });

    document.addEventListener("DOMContentLoaded", function () {
        var themeToggleButton = document.getElementById("themeToggleBtn");

        if (themeToggleButton) {
            themeToggleButton.addEventListener("click", function () {
                var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
                applyTheme(next);

                try {
                    localStorage.setItem(THEME_KEY, next);
                } catch (error) {
                    // Theme still applies for this session
                }
            });
        }

        // Re-enable transitions once the initial theme has been painted
        requestAnimationFrame(function () {
            requestAnimationFrame(function () {
                document.documentElement.classList.remove("preloading");
            });
        });
    });
})();
