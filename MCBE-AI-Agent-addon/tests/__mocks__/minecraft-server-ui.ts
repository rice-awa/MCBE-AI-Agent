// Mock for @minecraft/server-ui - provides minimal stubs for vitest unit tests

type ObservableSubscriber<T> = (value: T) => void;
type CustomFormInteraction = {
  clickButtonLabel?: string;
  failOnCustomFormCreate?: boolean;
  failOnObservableCreate?: boolean;
  failOnShow?: boolean;
  autoCloseAfterButtonClick?: boolean;
  fieldValues?: Record<string, unknown>;
};

const nextInteraction: CustomFormInteraction = {};

export function __resetDduiMock(): void {
  nextInteraction.clickButtonLabel = undefined;
  nextInteraction.failOnCustomFormCreate = false;
  nextInteraction.failOnObservableCreate = false;
  nextInteraction.failOnShow = false;
  nextInteraction.autoCloseAfterButtonClick = false;
  nextInteraction.fieldValues = undefined;
}

export function __setNextCustomFormInteraction(interaction: CustomFormInteraction): void {
  nextInteraction.clickButtonLabel = interaction.clickButtonLabel;
  nextInteraction.failOnCustomFormCreate = interaction.failOnCustomFormCreate ?? false;
  nextInteraction.failOnObservableCreate = interaction.failOnObservableCreate ?? false;
  nextInteraction.failOnShow = interaction.failOnShow ?? false;
  nextInteraction.autoCloseAfterButtonClick = interaction.autoCloseAfterButtonClick ?? false;
  nextInteraction.fieldValues = interaction.fieldValues;
}

class MockObservable<T> {
  #data: T;
  #subscribers = new Set<ObservableSubscriber<T>>();

  constructor(initialValue: T) {
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
  #fields = new Map<string, MockObservable<unknown>>();
  #buttons = new Map<string, () => void>();

  closeButton() { return this; }
  spacer() { return this; }
  label() { return this; }
  divider() { return this; }
  toggle(label: string, value: MockObservable<boolean>) {
    this.#fields.set(label, value as MockObservable<unknown>);
    return this;
  }
  slider(label: string, value: MockObservable<number>) {
    this.#fields.set(label, value as MockObservable<unknown>);
    return this;
  }
  dropdown(label: string, value: MockObservable<unknown>) {
    this.#fields.set(label, value);
    return this;
  }
  textField(label: string, value: MockObservable<string>) {
    this.#fields.set(label, value as MockObservable<unknown>);
    return this;
  }
  button(label: string, callback: () => void) {
    this.#buttons.set(label, callback);
    return this;
  }

  show() {
    if (nextInteraction.failOnShow) {
      return Promise.reject(new Error("Mock custom form show failed."));
    }
    this.#showing = true;
    return Promise.resolve().then(() => {
      for (const [label, value] of Object.entries(nextInteraction.fieldValues ?? {})) {
        this.#fields.get(label)?.setData(value);
      }
      if (nextInteraction.clickButtonLabel) {
        this.#buttons.get(nextInteraction.clickButtonLabel)?.();
      }
      if (nextInteraction.autoCloseAfterButtonClick) {
        this.#showing = false;
      }
    });
  }

  isShowing() {
    return this.#showing;
  }

  close() {
    this.#showing = false;
  }
}

export const Observable = {
  create<T>(initialValue: T) {
    if (nextInteraction.failOnObservableCreate) {
      throw new Error("Mock DDUI Observable API is unavailable.");
    }
    return new MockObservable(initialValue);
  },
};

export const CustomForm = {
  create() {
    if (nextInteraction.failOnCustomFormCreate) {
      throw new Error("Mock DDUI CustomForm API is unavailable.");
    }
    return new MockCustomForm();
  },
};

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
