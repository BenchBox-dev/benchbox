import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const REFUSED = 2;

export default function primaryCloneWriteGuard(pi: ExtensionAPI): void {
	pi.on("tool_call", async (event, ctx) => {
		try {
			const script = join(ctx.cwd, "scripts", "primary_clone_write_guard.py");
			if (!existsSync(script)) return;
			const result = spawnSync("python3", [script], {
				input: JSON.stringify({ tool_name: event.toolName, tool_input: event.input, cwd: ctx.cwd }),
				encoding: "utf8",
			});
			if (result.status === REFUSED) return { block: true, reason: result.stderr.trim() };
		} catch (error) {
			console.error(`primary-clone write guard failed open: ${error}`);
		}
	});
}
