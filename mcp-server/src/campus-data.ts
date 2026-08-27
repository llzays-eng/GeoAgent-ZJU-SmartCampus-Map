/**
 * campus-data.ts — Local gazetteer for the standalone geocode tool.
 *
 * Reads the SAME study_rooms.geojson / campus_pois.geojson files the Python
 * backend uses (../../data/*.geojson relative to this file), so there is one
 * source of truth for campus place names → coordinates, not a hand-copied
 * duplicate that could drift out of sync with the Python side.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// this file: mcp-server/src/campus-data.ts -> project root is two levels up
const PROJECT_ROOT = path.resolve(__dirname, "..", "..");
const DATA_DIR = path.join(PROJECT_ROOT, "data");

export interface GazetteerEntry {
  name: string;
  lat: number;
  lon: number;
}

interface GeoJsonFeature {
  type: "Feature";
  geometry: { type: string; coordinates: [number, number] };
  properties: Record<string, unknown>;
}

interface GeoJsonFeatureCollection {
  type: "FeatureCollection";
  features: GeoJsonFeature[];
}

function readFeatureCollection(filename: string): GeoJsonFeatureCollection {
  try {
    const raw = readFileSync(path.join(DATA_DIR, filename), "utf-8");
    const parsed = JSON.parse(raw) as GeoJsonFeatureCollection;
    if (parsed.type !== "FeatureCollection" || !Array.isArray(parsed.features)) {
      return { type: "FeatureCollection", features: [] };
    }
    return parsed;
  } catch {
    return { type: "FeatureCollection", features: [] };
  }
}

let cachedGazetteer: GazetteerEntry[] | null = null;

export function loadGazetteer(): GazetteerEntry[] {
  if (cachedGazetteer) return cachedGazetteer;

  const entries: GazetteerEntry[] = [];
  const seenNames = new Set<string>();

  for (const filename of ["study_rooms.geojson", "campus_pois.geojson"]) {
    const collection = readFeatureCollection(filename);
    for (const feature of collection.features) {
      const name = String(feature.properties?.name ?? "").trim();
      const coords = feature.geometry?.coordinates;
      if (!name || !coords || coords.length < 2 || seenNames.has(name)) continue;
      seenNames.add(name);
      entries.push({ name, lat: coords[1], lon: coords[0] });

      const building = feature.properties?.building;
      if (building && typeof building === "string" && !seenNames.has(building)) {
        seenNames.add(building);
        entries.push({ name: building, lat: coords[1], lon: coords[0] });
      }
    }
  }

  cachedGazetteer = entries;
  return entries;
}

/** Exact match, then substring match (preferring the most specific/longest
 * overlapping name when several match), then nothing — deliberately
 * simpler than the Python side's difflib fuzzy match; this server's job is
 * to demonstrate a real, independent MCP tool implementation, not to
 * duplicate every heuristic. */
export function matchGazetteer(address: string): GazetteerEntry | null {
  const trimmed = address.trim();
  if (!trimmed) return null;
  // Empty input needs its own guard: in JS, `"".includes(x)` AND
  // `x.includes("")` are both vacuously true for every non-empty x, so
  // without this check the substring branch below would treat a blank
  // query as matching (and return) the gazetteer's first entry instead of
  // correctly reporting "no match".

  const gazetteer = loadGazetteer();
  const exact = gazetteer.find((e) => e.name === trimmed);
  if (exact) return exact;

  // Among all substring matches, prefer the longest gazetteer name: a
  // longer overlap is a more specific/confident match, and — unlike a
  // plain array-order `.find()` — doesn't depend on incidental data
  // ordering (e.g. querying "紫金港" should not stop at whichever entry
  // happens to appear first in the gazetteer just because it also
  // contains/([is contained by]) the query).
  const candidates = gazetteer.filter((e) => trimmed.includes(e.name) || e.name.includes(trimmed));
  if (!candidates.length) return null;
  return candidates.reduce((best, e) => (e.name.length > best.name.length ? e : best));
}
