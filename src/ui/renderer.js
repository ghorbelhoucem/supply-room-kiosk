(() => {
  function patchHtml(el, html) {
    if (!el) return;
    el.innerHTML = html;
  }

  function setButtonBusy(btn, busy, busyLabel) {
    if (!btn) return;
    if (busy) {
      btn.dataset.originalLabel = btn.textContent;
      btn.textContent = busyLabel || "Loading...";
      btn.disabled = true;
      btn.setAttribute("aria-busy", "true");
    } else {
      btn.disabled = false;
      btn.setAttribute("aria-busy", "false");
      if (btn.dataset.originalLabel) btn.textContent = btn.dataset.originalLabel;
    }
  }

  function toSemanticButton(node) {
    if (!node || node.tagName === "BUTTON") return node;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = node.className;
    btn.innerHTML = node.innerHTML;
    Array.from(node.attributes).forEach((attr) => {
      if (attr.name !== "class") btn.setAttribute(attr.name, attr.value);
    });
    return btn;
  }

  function upgradeInteractiveElements(root) {
    if (!root) return;
    root.querySelectorAll(".menu-card, .item-row").forEach((node) => {
      if (node.tagName === "BUTTON") return;
      const btn = toSemanticButton(node);
      node.replaceWith(btn);
    });
    root.querySelectorAll(".banner-error").forEach((node) => node.setAttribute("role", "status"));
  }

  window.SupplyUi = { patchHtml, setButtonBusy, upgradeInteractiveElements };
})();
