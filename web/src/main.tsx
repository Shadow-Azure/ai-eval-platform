import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "antd/dist/reset.css";
import "./styles/tokens.css";
import "./styles/global.css";
import "./styles/shell.css";
import "./styles/overview.css";
import "./styles/login.css";
import App from "./app/App";

const container = document.getElementById("root");

if (!container) {
  throw new Error("Root container #root is missing");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
