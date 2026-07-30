(() => {
  function attachKeyboardScanner(opts) {
    const maxGapMs = opts.maxGapMs || 300;
    let scanBuffer = "";
    let lastScanKeyTime = 0;

    function onKeyDown(e) {
      const now = Date.now();
      if (now - lastScanKeyTime > maxGapMs) scanBuffer = "";
      lastScanKeyTime = now;

      if (e.key === "Enter") {
        const candidate = scanBuffer;
        scanBuffer = "";
        if (candidate.length >= 2) opts.onScan(candidate);
      } else if (e.key.length === 1) {
        scanBuffer += e.key;
      }
    }

    document.addEventListener("keydown", onKeyDown, { capture: true, passive: true });
    return () => document.removeEventListener("keydown", onKeyDown, { capture: true });
  }

  window.SupplyScanner = { attachKeyboardScanner };
})();
