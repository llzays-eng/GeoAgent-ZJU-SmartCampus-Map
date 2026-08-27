/**
 * index.ts — GeoAgent MCP server entry point.
 *
 * Outline section 11: a standalone Node.js/TypeScript MCP server, separate
 * from the Python backend, speaking the Model Context Protocol over stdio.
 * The point of this file existing at all is protocol-level interop — the
 * Python Orchestrator can reach this tool via mcp_bridge.py's MCP client
 * instead of an in-process function call, which is a genuinely different
 * integration than everything else in this project (which is all one
 * Python process).
 *
 * Exposes one tool: `geocode_campus_location`. Real AMap Web API call if
 * AMAP_API_KEY is set in the environment; otherwise falls back to the local
 * gazetteer built from the project's own data (campus-data.ts) — same
 * fallback *philosophy* as agent/tools/geocode_tool.py on the Python side,
 * implemented independently here rather than proxied.
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { matchGazetteer } from "./campus-data.js";

const server = new McpServer({
  name: "geoagent-campus-geocoder",
  version: "0.1.0",
});

async function geocodeViaAmap(address: string, apiKey: string): Promise<{ lat: number; lon: number; formatted: string } | null> {
  const url = new URL("https://restapi.amap.com/v3/geocode/geo");
  url.searchParams.set("address", address);
  url.searchParams.set("key", apiKey);
  url.searchParams.set("city", "杭州");

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 6000);
    const response = await fetch(url, { signal: controller.signal });
    clearTimeout(timeout);
    if (!response.ok) return null;

    const data = (await response.json()) as {
      status: string;
      geocodes?: Array<{ location: string; formatted_address: string }>;
    };
    if (data.status !== "1" || !data.geocodes?.length) return null;

    const [lonStr, latStr] = data.geocodes[0].location.split(",");
    return { lat: parseFloat(latStr), lon: parseFloat(lonStr), formatted: data.geocodes[0].formatted_address };
  } catch {
    return null;
  }
}

server.registerTool(
  "geocode_campus_location",
  {
    title: "Geocode a campus location",
    description:
      "将紫金港校区内的地址/地名解析为WGS-84经纬度坐标。优先调用高德地图API（需要环境变量 AMAP_API_KEY）；" +
      "未配置或调用失败时，退化为基于项目自有 study_rooms.geojson / campus_pois.geojson 数据构建的本地地名词典匹配。",
    inputSchema: {
      address: z.string().describe("地址或地名，例如「基础图书馆」或「北教学区」"),
    },
  },
  async ({ address }) => {
    address = address.trim();
    if (!address) {
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({ ok: false, source: "none", query: address, message: "地址不能为空" }, null, 2),
          },
        ],
        isError: false, // a validation rejection is a valid, well-formed answer, not a protocol error
      };
    }

    const apiKey = process.env.AMAP_API_KEY;
    if (apiKey) {
      const amapResult = await geocodeViaAmap(address, apiKey);
      if (amapResult) {
        return {
          content: [
            {
              type: "text",
              text: JSON.stringify({ ok: true, source: "amap", query: address, ...amapResult }, null, 2),
            },
          ],
        };
      }
    }

    const match = matchGazetteer(address);
    if (match) {
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(
              {
                ok: true,
                source: "local_gazetteer",
                query: address,
                matched_address: match.name,
                lat: match.lat,
                lon: match.lon,
                degraded: true,
                degraded_reason: "未配置 AMAP_API_KEY 或高德接口不可用，已退化为本地地名词典匹配（数据来源：项目自有 data/*.geojson）。",
              },
              null,
              2
            ),
          },
        ],
      };
    }

    return {
      content: [
        {
          type: "text",
          text: JSON.stringify(
            { ok: false, source: "none", query: address, message: "高德API不可用/未配置，且本地地名词典中无匹配项。" },
            null,
            2
          ),
        },
      ],
      isError: false, // a "not found" result is a valid, well-formed answer, not a protocol error
    };
  }
);

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("GeoAgent MCP server running on stdio"); // stderr only — stdout is reserved for the MCP protocol
}

main().catch((err) => {
  console.error("Fatal error starting MCP server:", err);
  process.exit(1);
});
