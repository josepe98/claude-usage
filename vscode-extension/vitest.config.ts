import { defineConfig } from "vitest/config";

export default defineConfig({
	test: {
		include: ["src/__tests__/**/*.test.ts"],
		environment: "node",
		// `vscode` is only available inside the editor host. Tests mock it
		// out so importing `extension.ts` (which `import * as vscode from "vscode"`)
		// does not blow up under plain Node.
		alias: {
			vscode: new URL("./src/__tests__/vscode-mock.ts", import.meta.url).pathname,
		},
	},
});
