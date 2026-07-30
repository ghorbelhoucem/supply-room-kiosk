(() => {
  function normalizeText(s) {
    return String(s).trim().toLowerCase().replace(/\s+/g, " ");
  }

  function availableOf(itemName, inventoryMap, historyRows) {
    const inv = inventoryMap[itemName];
    if (!inv) return 0;
    if (inv.reference === "Station Parts") return Number(inv.quantity) || 0;
    const total = Number(inv.quantity) || 0;
    const missing = historyRows.filter((h) => h.item === itemName && h.returnedAt === "Not returned").length;
    return Math.max(0, total - missing);
  }

  function openCheckoutsFor(personCode, inventoryMap, historyRows) {
    return historyRows.filter((h) => {
      const inv = inventoryMap[h.item];
      return h.personRole === personCode && inv && inv.reference === "Tools" && h.returnedAt === "Not returned";
    });
  }

  function allOpenToolCheckouts(inventoryMap, historyRows) {
    return historyRows.filter((h) => {
      const inv = inventoryMap[h.item];
      return inv && inv.reference === "Tools" && h.returnedAt === "Not returned";
    });
  }

  function isOverdue(h) {
    if (h.returnedAt !== "Not returned") return false;
    if (!h.expectedReturn || h.expectedReturn === "None") return false;
    const t = new Date(h.expectedReturn).getTime();
    return !Number.isNaN(t) && t < Date.now();
  }

  function fmtDate(d) {
    if (!d || d === "None" || d === "Not returned" || d === "N/A") return d || "-";
    const dt = new Date(d);
    if (Number.isNaN(dt.getTime())) return String(d);
    return (
      dt.toLocaleDateString(undefined, { month: "short", day: "numeric" }) +
      " " +
      dt.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
    );
  }

  window.SupplyDomain = {
    normalizeText,
    availableOf,
    openCheckoutsFor,
    allOpenToolCheckouts,
    isOverdue,
    fmtDate,
  };
})();
