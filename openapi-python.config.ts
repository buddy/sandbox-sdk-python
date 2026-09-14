import { defineConfig } from "@hey-api/openapi-python";

export default defineConfig({
	// Buddy's published OpenAPI document. Pass `-i <url>` to generate from another.
	input: "https://es.buddy.works/openapi/production/restapi.json",
	output: {
		path: "src/buddy_sandbox/api/openapi",
		postProcess: [
			{
				command: "python",
				args: ["scripts/cleanup_schemas.py"],
			},
			"ruff:format",
		],
	},
	parser: {
		filters: {
			tags: {
				include: ["Sandbox API", "Workspace API"],
			},
		},
	},
	plugins: [
		{
			name: "pydantic",
			enums: "literal",
		},
	],
});
