/**
 * Tiny stub of the `vscode` namespace so `extension.ts` can be imported under
 * Node for unit-testing the pure helpers. Only the API surface we touch is
 * stubbed — anything else will throw, which is the correct signal that a
 * test is straying into editor-host territory.
 */
export const StatusBarAlignment = { Left: 1, Right: 2 } as const;

export class MarkdownString {
	constructor(public value: string = "") {}
}

export class ThemeColor {
	constructor(public id: string) {}
}

export class Uri {
	private constructor(public raw: string) {}
	static parse(s: string): Uri { return new Uri(s); }
}

export const window = {
	createStatusBarItem: () => ({
		text: "",
		tooltip: "",
		command: "",
		backgroundColor: undefined as unknown,
		show() {},
		hide() {},
		dispose() {},
	}),
	showWarningMessage: async (_m: string) => undefined,
};

export const workspace = {
	getConfiguration: (_section?: string) => ({
		get<T>(_key: string): T | undefined { return undefined; },
	}),
	onDidChangeConfiguration: (_h: unknown) => ({ dispose() {} }),
};

export const commands = {
	registerCommand: (_id: string, _cb: unknown) => ({ dispose() {} }),
};

export const env = {
	openExternal: async (_u: Uri) => true,
};
