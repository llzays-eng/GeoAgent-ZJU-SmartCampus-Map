import { getPois, getStudyRooms } from './api'

export const EMPTY_FEATURE_COLLECTION = {
  type: 'FeatureCollection',
  features: []
}

const datasetLoaders = {
  studyRooms: getStudyRooms,
  pois: getPois
}

export function textOrUnknown(value) {
  if (value === null || value === undefined || value === '') {
    return '未知'
  }

  if (Array.isArray(value)) {
    return value.length ? value.join('、') : '未知'
  }

  if (typeof value === 'boolean') {
    return value ? '是' : '否'
  }

  return String(value)
}

export function normalizeFeatureCollection(data) {
  if (!data || data.type !== 'FeatureCollection' || !Array.isArray(data.features)) {
    return EMPTY_FEATURE_COLLECTION
  }

  return data
}

export function getFeatureCoordinate(feature) {
  const coordinates = feature?.geometry?.coordinates

  if (
    feature?.geometry?.type !== 'Point' ||
    !Array.isArray(coordinates) ||
    coordinates.length < 2
  ) {
    return null
  }

  const [longitude, latitude] = coordinates
  if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) {
    return null
  }

  return { latitude, longitude }
}

async function loadDataset(key) {
  const loader = datasetLoaders[key]

  if (!loader) {
    return {
      data: EMPTY_FEATURE_COLLECTION,
      error: '未知数据源。'
    }
  }

  try {
    const response = await loader()
    return {
      data: normalizeFeatureCollection(response.data),
      error: ''
    }
  } catch (error) {
    return {
      data: EMPTY_FEATURE_COLLECTION,
      error: '后端暂不可用，当前展示空数据。'
    }
  }
}

/* ── Coordinate transforms (BD-09 ↔ GCJ-02 ↔ WGS84) ───── */

const EARTH_RADIUS = 6378245.0
const EE = 0.006693421622965943
const X_PI = (Math.PI * 3000.0) / 180.0

function transformLat(lng, lat) {
  let r = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + 0.1 * lng * lat + 0.2 * Math.sqrt(Math.abs(lng))
  r += ((20.0 * Math.sin(6.0 * lng * Math.PI) + 20.0 * Math.sin(2.0 * lng * Math.PI)) * 2.0) / 3.0
  r += ((20.0 * Math.sin(lat * Math.PI) + 40.0 * Math.sin((lat / 3.0) * Math.PI)) * 2.0) / 3.0
  r += ((160.0 * Math.sin((lat / 12.0) * Math.PI) + 320 * Math.sin((lat * Math.PI) / 30.0)) * 2.0) / 3.0
  return r
}

function transformLon(lng, lat) {
  let r = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + 0.1 * lng * lat + 0.1 * Math.sqrt(Math.abs(lng))
  r += ((20.0 * Math.sin(6.0 * lng * Math.PI) + 20.0 * Math.sin(2.0 * lng * Math.PI)) * 2.0) / 3.0
  r += ((20.0 * Math.sin(lng * Math.PI) + 40.0 * Math.sin((lng / 3.0) * Math.PI)) * 2.0) / 3.0
  r += ((150.0 * Math.sin((lng / 12.0) * Math.PI) + 300.0 * Math.sin((lng / 30.0) * Math.PI)) * 2.0) / 3.0
  return r
}

export function gcj02ToWgs84(latitude, longitude) {
  const dLat = transformLat(longitude - 105.0, latitude - 35.0)
  const dLon = transformLon(longitude - 105.0, latitude - 35.0)
  const radLat = (latitude / 180.0) * Math.PI
  let magic = Math.sin(radLat)
  magic = 1 - EE * magic * magic
  const sqrtMagic = Math.sqrt(magic)
  const adjustedLat = (dLat * 180.0) / (((EARTH_RADIUS * (1 - EE)) / (magic * sqrtMagic)) * Math.PI)
  const adjustedLon = (dLon * 180.0) / ((EARTH_RADIUS / sqrtMagic) * Math.cos(radLat) * Math.PI)
  return {
    latitude: latitude * 2 - (latitude + adjustedLat),
    longitude: longitude * 2 - (longitude + adjustedLon)
  }
}

export function bd09ToGcj02(latitude, longitude) {
  const x = longitude - 0.0065
  const y = latitude - 0.006
  const z = Math.sqrt(x * x + y * y) - 0.00002 * Math.sin(y * X_PI)
  const theta = Math.atan2(y, x) - 0.000003 * Math.cos(x * X_PI)
  return { latitude: z * Math.sin(theta), longitude: z * Math.cos(theta) }
}

export function bd09ToWgs84(coordinate) {
  const gcj = bd09ToGcj02(coordinate.latitude, coordinate.longitude)
  return gcj02ToWgs84(gcj.latitude, gcj.longitude)
}

export async function loadMapDatasets() {
  const [studyRooms, pois] = await Promise.all([
    loadDataset('studyRooms'),
    loadDataset('pois')
  ])

  return {
    studyRooms,
    pois
  }
}
