(() => {
  const transitions = {
    idle: ["deptPin", "operatorId", "report"],
    deptPin: ["idle", "deptPinPickName", "menu"],
    deptPinPickName: ["deptPin", "menu"],
    operatorId: ["idle", "operatorPassword"],
    operatorPassword: ["operatorId", "menu"],
    menu: ["idle", "scanPrompt", "takeCategory", "return", "report", "restockCategory"],
    restockCategory: ["menu", "restockPick"],
    restockPick: ["restockCategory", "confirm"],
    scanPrompt: ["menu", "takeCategory", "scanConfirm", "basketReview"],
    scanConfirm: ["scanPrompt", "takeCategory", "scanTakeDetail"],
    scanTakeDetail: ["scanPrompt", "basketReview"],
    takeCategory: ["menu", "take"],
    take: ["takeCategory", "basketReview"],
    basketReview: ["scanPrompt", "menu", "confirm"],
    return: ["menu", "returnScanPrompt", "returnBasketReview"],
    returnScanPrompt: ["return", "returnBasketReview"],
    returnBasketReview: ["return", "menu", "confirm"],
    confirm: ["idle"],
    report: ["idle"],
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
