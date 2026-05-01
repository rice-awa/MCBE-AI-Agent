// Mock for @minecraft/server-ui - provides minimal stubs for vitest unit tests

export const ActionFormData = class {
  title() { return this; }
  body() { return this; }
  button() { return this; }
  divider() { return this; }
  header() { return this; }
  label() { return this; }
  show() { return Promise.resolve({ canceled: false }); }
};

export const ModalFormData = class {
  title() { return this; }
  slider() { return this; }
  toggle() { return this; }
  dropdown() { return this; }
  textField() { return this; }
  submitButton() { return this; }
  divider() { return this; }
  header() { return this; }
  label() { return this; }
  show() { return Promise.resolve({ canceled: false }); }
};
