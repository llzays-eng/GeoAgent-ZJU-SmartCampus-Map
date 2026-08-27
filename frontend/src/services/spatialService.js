/**
 * spatialService.js — Pure-frontend spatial analysis helpers
 *
 * Operates on already-loaded GeoJSON Feature arrays (Point geometry).
 * No network round-trips required; the dataset (≤ 50 features) is small
 * enough that Haversine brute-force is instant.
 */

const EARTH_RADIUS_M = 6_371_000

/**
 * Haversine distance (metres) between two WGS-84 coordinates.
 *
 * @param {number} lat1
 * @param {number} lon1
 * @param {number} lat2
 * @param {number} lon2
 * @returns {number}
 */
export function haversineDistance(lat1, lon1, lat2, lon2) {
  const toRad = (deg) => (deg * Math.PI) / 180
  const dLat = toRad(lat2 - lat1)
  const dLon = toRad(lon2 - lon1)
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a))
}

/**
 * Extract [lng, lat] from a GeoJSON Point feature, or null if invalid.
 *
 * @param {object} feature
 * @returns {[number, number] | null}
 */
function getPointCoords(feature) {
  const coords = feature?.geometry?.coordinates
  if (!Array.isArray(coords) || coords.length < 2) return null
  const [lon, lat] = coords
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null
  return [lon, lat]
}

/**
 * Return all features whose centroid lies within `radiusM` metres of
 * (centerLat, centerLng), sorted by distance ascending.
 *
 * @param {number} centerLat
 * @param {number} centerLng
 * @param {number} radiusM       Buffer radius in metres.
 * @param {object[]} features    GeoJSON Feature array.
 * @returns {{ feature: object, distance_m: number }[]}
 */
export function findFeaturesWithinBuffer(centerLat, centerLng, radiusM, features) {
  const results = []
  for (const feature of features) {
    const pt = getPointCoords(feature)
    if (!pt) continue
    const [lon, lat] = pt
    const dist = haversineDistance(centerLat, centerLng, lat, lon)
    if (dist <= radiusM) {
      results.push({ feature, distance_m: Math.round(dist) })
    }
  }
  return results.sort((a, b) => a.distance_m - b.distance_m)
}

/**
 * Return the nearest `limit` features from (lat, lng),
 * optionally filtered by `feature.properties.category`.
 *
 * @param {number}   lat
 * @param {number}   lng
 * @param {object[]} features
 * @param {number}   [limit=8]
 * @param {string}   [category='']   Empty string = no filter.
 * @returns {{ feature: object, distance_m: number }[]}
 */
export function findNearestFeatures(lat, lng, features, limit = 8, category = '') {
  const results = []
  for (const feature of features) {
    const pt = getPointCoords(feature)
    if (!pt) continue
    const [lon, featLat] = pt
    const props = feature.properties || {}
    if (category && props.category !== category) continue
    const dist = haversineDistance(lat, lng, featLat, lon)
    results.push({ feature, distance_m: Math.round(dist) })
  }
  return results.sort((a, b) => a.distance_m - b.distance_m).slice(0, limit)
}
