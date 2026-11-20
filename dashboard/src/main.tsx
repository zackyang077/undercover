import "@fontsource-variable/instrument-sans";
import "@fontsource/instrument-serif/latin-400-italic.css";
import "@fontsource-variable/jetbrains-mono";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HandlerSession } from "./HandlerSession";

import { watchTheme } from "./theme";

const stopWatchingTheme = watchTheme();
import.meta.hot?.dispose(stopWatchingTheme);

const root = document.getElementById("root");
if (!root) throw new Error("index.html has no #root element");

createRoot(root).render(
  <StrictMode>
    <HandlerSession />
  </StrictMode>,
);
