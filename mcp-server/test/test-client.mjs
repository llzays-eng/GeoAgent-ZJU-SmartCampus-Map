// test/test-client.mjs — spawns the REAL compiled MCP server as a child
// process and calls its tool through the actual MCP client SDK over stdio.
// This is not a mock: it exercises the genuine protocol handshake, tool
// listing, and tool invocation round trip. Runs fully offline (uses the
// local-gazetteer fallback path, since no AMAP_API_KEY is set here and
// restapi.amap.com isn't reachable in the sandbox this was built in).
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

async function main() {
  const transport = new StdioClientTransport({
    command: "node",
    args: ["dist/index.js"],
  });

  const client = new Client({ name: "geoagent-test-client", version: "0.1.0" });
  await client.connect(transport);
  console.log("✓ Connected to MCP server over stdio");

  const tools = await client.listTools();
  console.log(`✓ Server exposes ${tools.tools.length} tool(s):`, tools.tools.map((t) => t.name));
  if (!tools.tools.some((t) => t.name === "geocode_campus_location")) {
    throw new Error("expected tool 'geocode_campus_location' not found");
  }

  const cases = [
    { address: "基础图书馆", expectMatch: true },
    { address: "北教学区", expectMatch: true },
    { address: "这是一个完全不存在的地名ABCXYZ", expectMatch: false },
    // Regression case: in JS, `"".includes(x)` and `x.includes("")` are both
    // vacuously true, so a naive substring match previously returned the
    // gazetteer's first entry for a blank address instead of "no match".
    { address: "", expectMatch: false },
    { address: "   ", expectMatch: false },
  ];

  for (const { address, expectMatch } of cases) {
    const result = await client.callTool({ name: "geocode_campus_location", arguments: { address } });
    const text = result.content[0].text;
    const parsed = JSON.parse(text);
    console.log(`  geocode("${address}") ->`, parsed.ok ? `ok, source=${parsed.source}, (${parsed.lat}, ${parsed.lon})` : `ok=false: ${parsed.message}`);
    if (parsed.ok !== expectMatch) {
      throw new Error(`unexpected result for "${address}": expected ok=${expectMatch}, got ok=${parsed.ok}`);
    }
  }

  await client.close();
  console.log("\n✓ ALL MCP SERVER TESTS PASSED (real protocol round trip, real gazetteer data)");
}

main().catch((err) => {
  console.error("✗ TEST FAILED:", err);
  process.exit(1);
});
