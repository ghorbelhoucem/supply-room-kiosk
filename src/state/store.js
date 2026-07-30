(() => {
  function createStore(machine, initialState) {
    let state = { ...initialState };
    const listeners = [];

    function getState() {
      return state;
    }

    function setState(partial) {
      state = { ...state, ...partial };
      listeners.forEach((l) => l(state));
    }

    function transition(nextState) {
      const from = state.current;
      if (!machine.canTransition(from, nextState)) {
        return { ok: false, error: `Invalid transition: ${from} -> ${nextState}` };
      }
      setState({ current: nextState });
      return { ok: true };
    }

    function subscribe(fn) {
      listeners.push(fn);
      return () => {
        const idx = listeners.indexOf(fn);
        if (idx >= 0) listeners.splice(idx, 1);
      };
    }

    return { getState, setState, transition, subscribe };
  }

  window.SupplyStore = { createStore };
})();
