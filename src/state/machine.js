(() => {
  const transitions = {
    idle: ["nameSelect", "operatorId", "report"],
    nameSelect: ["idle", "pinEntry"],
    pinEntry: ["nameSelect", "menu"],
    operatorId: ["idle", "menu"],
    menu: ["idle", "scanPrompt", "takeCategory", "return", "report"],
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
