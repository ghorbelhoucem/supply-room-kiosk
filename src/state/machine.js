(() => {
  const transitions = {
    roomSelect: ["idle", "menu"],
    idle: ["roomSelect", "deptPin", "operatorId", "report"],
    deptPin: ["idle", "deptPinPickName", "menu"],
    deptPinPickName: ["deptPin", "menu"],
    operatorId: ["idle", "operatorPassword"],
    operatorPassword: ["operatorId", "menu"],
    menu: ["roomSelect", "scanPrompt", "takeCategory", "return", "report", "restockCategory", "warranty", "furnitureCategories", "furnitureLocations", "furnitureItems", "furnitureFloors"],
    furnitureCategories: ["menu", "furnitureItems", "furnitureAdd"],
    furnitureLocations: ["menu", "furnitureItems"],
    furnitureItems: ["menu", "furnitureCategories", "furnitureLocations", "furnitureItem", "furnitureAdd"],
    furnitureItem: ["furnitureItems", "furnitureEditStock", "furnitureEditItem", "furnitureRemove"],
    furnitureEditStock: ["furnitureItem"],
    furnitureEditItem: ["furnitureItem"],
    furnitureRemove: ["furnitureItem", "furnitureItems"],
    furnitureAdd: ["furnitureItem", "furnitureItems", "furnitureCategories"],
    furnitureFloors: ["menu", "furnitureRooms", "furnitureRoom"],
    furnitureRooms: ["furnitureFloors", "furnitureRoom", "furnitureRoomEdit"],
    furnitureRoom: ["furnitureRooms", "furnitureRoomItem", "furnitureRoomAdd", "furnitureRoomEdit"],
    furnitureRoomItem: ["furnitureRoom", "furnitureRoomMove", "furnitureRoomRemove", "furnitureRoomItemEdit", "furnitureRoomMerge"],
    furnitureRoomMove: ["furnitureRoomItem", "furnitureRoom"],
    furnitureRoomAdd: ["furnitureRoom"],
    furnitureRoomRemove: ["furnitureRoomItem", "furnitureRoom"],
    furnitureRoomEdit: ["furnitureRoom", "furnitureRooms"],
    furnitureRoomItemEdit: ["furnitureRoomItem"],
    furnitureRoomMerge: ["furnitureRoomItem", "furnitureRoom", "furnitureRooms"],
    restockCategory: ["menu", "restockPick", "restockBasketReview"],
    warranty: ["menu", "confirm"],
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
    report: ["roomSelect", "idle", "menu", "return", "restockCategory", "takeCategory", "scanPrompt", "itemEdit", "itemAdd"],
    itemEdit: ["report", "itemDelete"],
    itemAdd: ["report"],
    itemDelete: ["itemEdit", "report"],
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
