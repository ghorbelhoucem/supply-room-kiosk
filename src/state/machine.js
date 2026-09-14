(() => {
  const transitions = {
    roomSelect: ["idle", "menu"],
    idle: ["roomSelect", "deptPin", "operatorId", "report"],
    deptPin: ["idle", "deptPinPickName", "menu"],
    deptPinPickName: ["deptPin", "menu"],
    operatorId: ["idle", "operatorPassword"],
    operatorPassword: ["operatorId", "menu"],
    menu: ["roomSelect", "scanPrompt", "takeCategory", "return", "report", "restockCategory"],
    restockCategory: ["menu", "restockPick", "restockBasketReview"],
    restockPick: ["restockCategory", "restockBasketReview", "menu"],
    restockBasketReview: ["restockCategory", "menu", "confirm"],
    scanPrompt: ["menu", "takeCategory", "scanConfirm", "basketReview"],
    scanConfirm: ["scanPrompt", "takeCategory", "scanTakeDetail", "menu"],
    scanTakeDetail: ["scanPrompt", "basketReview"],
    takeCategory: ["menu", "take"],
    take: ["takeCategory", "basketReview", "menu"],
    basketReview: ["scanPrompt", "menu", "confirm"],
    return: ["menu", "returnScanPrompt", "returnBasketReview"],
    returnScanPrompt: ["return", "returnBasketReview"],
    returnBasketReview: ["return", "menu", "confirm"],
    confirm: ["roomSelect", "idle", "menu"],
    report: ["roomSelect", "idle", "menu", "return", "restockCategory", "takeCategory", "scanPrompt"],
  };

  function createSupplyMachine() {
    return {
      canTransition(from, to) {
        return Boolean(transitions[from] && transitions[from].includes(to));
      },
      transitions,
    };
  }

  window.SupplyMachine = { createSupplyMachine };
})();
