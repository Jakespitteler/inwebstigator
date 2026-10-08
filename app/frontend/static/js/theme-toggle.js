// The light / dark mode button. The saved theme is first applied by a script in the page's <head>.
// The dashboard's scripts share one scope, so they are loaded in order (see templates/index.html).

// Light / dark mode toggle
const THEME_KEY = "inwebstigator-theme";

function applyTheme(theme) {
    document.documentElement.setAttribute(
        "data-theme",
        theme
    );
}

function hasSavedTheme() {
    try {
        return localStorage.getItem(THEME_KEY) !== null;
    } catch (error) {
        return false;
    }
}

const themeToggleButton =
    document.getElementById(
        "themeToggleBtn"
    );

if (themeToggleButton) {

    themeToggleButton.addEventListener(
        "click",
        () => {

            const current =
                document.documentElement.getAttribute(
                    "data-theme"
                );

            const next =
                current === "dark" ? "light" : "dark";

            applyTheme(next);

            try {
                localStorage.setItem(THEME_KEY, next);
            } catch (error) {
                // Theme still applies for this session
            }
        }
    );
}

// Follow the OS setting until the user picks a theme themselves
window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener(
        "change",
        (event) => {
            if (!hasSavedTheme()) {
                applyTheme(event.matches ? "dark" : "light");
            }
        }
    );

// Re-enable transitions once the initial theme has been painted
requestAnimationFrame(() =>
    requestAnimationFrame(() =>
        document.documentElement.classList.remove("preloading")
    )
);
