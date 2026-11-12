import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const REFUSED = 2;
const WRITE_TOOLS: Record<string, true> = { write: true, edit: true, ast_edit: true, apply_patch: true, notebook: true };

function runGuard(script: string, payload: string): Promise<{ status: number | null; stderr: string }> {
	const { promise, resolve } = Promise.withResolvers<{ status: number | null; stderr: string }>();
	const child = spawn("python3", [script], { stdio: ["pipe", "ignore", "pipe"] });
	let stderr = "";
	child.stderr.setEncoding("utf8");
	child.stderr.on("data", chunk => {
		stderr += chunk;
	});
	child.on("error", () => resolve({ status: null, stderr }));
	child.on("close", status => resolve({ status, stderr }));
	child.stdin.on("error", () => {});
	child.stdin.end(payload);
	return promise;
}

export default function primaryCloneWriteGuard(pi: ExtensionAPI): void {
	pi.on("tool_call", async (event, ctx) => {
		if (!Object.hasOwn(WRITE_TOOLS, String(event.toolName).toLowerCase())) return;
		try {
			const script = join(ctx.cwd, "scripts", "primary_clone_write_guard.py");
			if (!existsSync(script)) return;
			const payload = JSON.stringify({ tool_name: event.toolName, tool_input: event.input, cwd: ctx.cwd });
			const result = await runGuard(script, payload);
			if (result.status === REFUSED) return { block: true, reason: result.stderr.trim() };
		} catch (error) {
			console.error(`primary-clone write guard failed open: ${error}`);
		}
	});
}
