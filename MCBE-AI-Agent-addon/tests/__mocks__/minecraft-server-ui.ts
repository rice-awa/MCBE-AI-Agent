// Mock for @minecraft/server-ui - provides minimal stubs for vitest unit tests
// Adapted for @minecraft/server-ui v2.1.0 (DDUI stable API)

type ObservableSubscriber<T> = (value: T) => void;
type CustomFormInteraction = {
  clickButtonLabel?: string;
  closeReason?: unknown;
  failOnCustomFormCreate?: boolean;
  failOnObservableCreate?: boolean;
  failOnShow?: boolean;
  autoCloseAfterButtonClick?: boolean;
  fieldValues?: Record<string, unknown>;
};

const nextInteraction: CustomFormInteraction = {};
let lastCustomForm: MockCustomForm | undefined;

export function __getLastCustomForm(): MockCustomForm | undefined {
  return lastCustomForm;
}

export function __resetDduiMock(): void {
  nextInteraction.clickButtonLabel = undefined;
  nextInteraction.closeReason = undefined;
  nextInteraction.failOnCustomFormCreate = false;
  nextInteraction.failOnObservableCreate = false;
  nextInteraction.failOnShow = false;
  nextInteraction.autoCloseAfterButtonClick = false;
  nextInteraction.fieldValues = undefined;
  lastCustomForm = undefined;
}

export function __setNextCustomFormInteraction(interaction: CustomFormInteraction): void {
  nextInteraction.clickButtonLabel = interaction.clickButtonLabel;
  nextInteraction.closeReason = interaction.closeReason;
  nextInteraction.failOnCustomFormCreate = interaction.failOnCustomFormCreate ?? false;
  nextInteraction.failOnObservableCreate = interaction.failOnObservableCreate ?? false;
  nextInteraction.failOnShow = interaction.failOnShow ?? false;
  nextInteraction.autoCloseAfterButtonClick = interaction.autoCloseAfterButtonClick ?? false;
  nextInteraction.fieldValues = interaction.fieldValues;
}

class MockObservable<T> {
  #data: T;
  #subscribers = new Set<ObservableSubscriber<T>>();

  constructor(initialValue: T, _options?: { clientWritable?: boolean }) {
    if (nextInteraction.failOnObservableCreate) {
      throw new Error("Mock observable create failed.");
    }
    this.#data = initialValue;
  }

  getData() {
    return this.#data;
  }

  setData(value: T) {
    this.#data = value;
    for (const subscriber of this.#subscribers) {
      subscriber(value);
    }
  }

  subscribe(subscriber: ObservableSubscriber<T>) {
    this.#subscribers.add(subscriber);
    return subscriber;
  }

  unsubscribe(subscriber?: ObservableSubscriber<T>) {
    if (!subscriber) {
      this.#subscribers.clear();
      return;
    }
    this.#subscribers.delete(subscriber);
  }
}

class MockCustomForm {
  #showing = false;
  #programmaticallyClosed = false;
  #fields = new Map<string, MockObservable<unknown>>();
  #buttons = new Map<string, () => void>();
  #labels: Array<string | MockObservable<unknown>> = [];
  #components: string[] = [];

  constructor() {
    lastCustomForm = this;
  }
  closeButton() {
    this.#components.push("closeButton");
    return this;
  }
  spacer() {
    this.#components.push("spacer");
    return this;
  }
  header() {
    this.#components.push("header");
    return this;
  }
  label(value: string | MockObservable<unknown>) {
    this.#labels.push(value);
    this.#components.push("label");
    return this;
  }
  divider() {
    this.#components.push("divider");
    return this;
  }
  toggle(label: string, value: MockObservable<boolean>) {
    this.#fields.set(label, value as MockObservable<unknown>);
    this.#components.push("toggle");
    return this;
  }
  slider(label: string, value: MockObservable<number>) {
    this.#fields.set(label, value as MockObservable<unknown>);
    this.#components.push("slider");
    return this;
  }
  dropdown(label: string, value: MockObservable<unknown>) {
    this.#fields.set(label, value);
    this.#components.push("dropdown");
    return this;
  }
  textField(label: string, value: MockObservable<string>) {
    this.#fields.set(label, value as MockObservable<unknown>);
    this.#components.push("textField");
    return this;
  }
  button(label: string, callback: () => void) {
    this.#buttons.set(label, callback);
    this.#components.push(`button:${label}`);
    return this;
  }

  getFieldData(label: string): unknown {
    return this.#fields.get(label)?.getData();
  }

  getLabelTexts(): unknown[] {
    return this.#labels.map((value) => (typeof value === "string" ? value : value.getData()));
  }

  getComponents(): string[] {
    return [...this.#components];
  }

  clickButton(label: string): void {
    this.#buttons.get(label)?.();
  }

  show() {
    if (nextInteraction.failOnShow) {
      return Promise.reject(new Error("Mock custom form show failed."));
    }
    this.#showing = true;
    this.#programmaticallyClosed = false;
    return Promise.resolve().then(() => {
      // Field values are set before the button callback so the callback
      // reads the simulated user input (e.g. message text, toggles).
      for (const [label, value] of Object.entries(nextInteraction.fieldValues ?? {})) {
        this.#fields.get(label)?.setData(value);
      }
      if (nextInteraction.clickButtonLabel) {
        this.#buttons.get(nextInteraction.clickButtonLabel)?.();
      }
      if (nextInteraction.autoCloseAfterButtonClick) {
        this.#showing = false;
      }
      // If close() was called programmatically (e.g. in a button callback)
      // return ServerClose so showCustomFormSafely treats it as intentional.
      if (this.#programmaticallyClosed) {
        return nextInteraction.closeReason ?? "ServerClose";
      }
      return nextInteraction.closeReason ?? (this.#showing ? "ServerClose" : "UserClose");
    });
  }

  isShowing() {
    return this.#showing;
  }

  close() {
    this.#programmaticallyClosed = true;
    this.#showing = false;
  }
}

// Export v2.1.0 DDUI API: Observable* as constructors, CustomForm as constructor, DataDrivenScreenClosedReason enum
export const ObservableString = MockObservable<string>;
export const ObservableNumber = MockObservable<number>;
export const ObservableBoolean = MockObservable<boolean>;
export const CustomForm = MockCustomForm;

export const DataDrivenScreenClosedReason = {
  ClientClosed: "ClientClosed",
  ServerClosed: "ServerClosed",
  UserBusy: "UserBusy",
};

export const ActionFormData = class {
  title() {
    return this;
  }
  body() {
    return this;
  }
  button() {
    return this;
  }
  divider() {
    return this;
  }
  header() {
    return this;
  }
  label() {
    return this;
  }
  show() {
    return Promise.resolve({ canceled: false });
  }
};

export const ModalFormData = class {
  title() {
    return this;
  }
  slider() {
    return this;
  }
  toggle() {
    return this;
  }
  dropdown() {
    return this;
  }
  textField() {
    return this;
  }
  submitButton() {
    return this;
  }
  divider() {
    return this;
  }
  header() {
    return this;
  }
  label() {
    return this;
  }
  show() {
    return Promise.resolve({ canceled: false });
  }
};
