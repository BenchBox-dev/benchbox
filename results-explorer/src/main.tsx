import { render } from "preact";
import { App } from "./App";
import "@fontsource-variable/instrument-sans/wght.css";
import "@fontsource-variable/martian-mono/wdth.css";
import "./index.css";

const SPA_REDIRECT_KEY = "benchbox.results.redirect";

if (
  (window.location.pathname === "/results/" || window.location.pathname === "/results/index.html") &&
  typeof window.sessionStorage !== "undefined"
) {
  try {
    const restoredRoute = window.sessionStorage.getItem(SPA_REDIRECT_KEY);
    if (restoredRoute?.startsWith("/results/")) {
      window.sessionStorage.removeItem(SPA_REDIRECT_KEY);
      window.history.replaceState(null, "", restoredRoute);
    }
  } catch {
  }
}

const root = document.getElementById("app");
if (!root) throw new Error("Root element #app not found");

root.replaceChildren();
render(<App />, root);
