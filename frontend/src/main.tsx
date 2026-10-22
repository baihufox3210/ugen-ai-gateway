import React from "react";
import ReactDOM from "react-dom/client";

import Root from "./Root.tsx";
import { applyTheme, loadTheme } from "./theme";
// Fonts are served by the gateway itself: no request leaves for a font CDN.
import "@fontsource/dm-sans/400.css";
import "@fontsource/dm-sans/500.css";
import "@fontsource/dm-sans/600.css";
import "@fontsource/dm-sans/700.css";
import "@fontsource/dm-sans/400-italic.css";
import "@fontsource/dm-sans/700-italic.css";
// Chinese: regular and bold only; other weights use the nearest one.
import "@fontsource/noto-sans-tc/400.css";
import "@fontsource/noto-sans-tc/700.css";
import "./style.css";

applyTheme(loadTheme());

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
);
