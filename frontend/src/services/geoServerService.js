import axios from 'axios'
import { EMPTY_FEATURE_COLLECTION } from './geojsonData'

const api = axios.create({ timeout: 8000 })

function getBaseUrl() {
  return (import.meta.env.VITE_GEOSERVER_URL || 'http://127.0.0.1:8080/geoserver').replace(
    /\/$/,
    ''
  )
}

function getWorkspace() {
  return import.meta.env.VITE_GEOSERVER_WORKSPACE || 'webgis'
}

function getLayer() {
  return import.meta.env.VITE_GEOSERVER_LAYER || 'campus_pois'
}

function isEnabled() {
  const value = String(import.meta.env.VITE_USE_GEOSERVER || 'false').trim().toLowerCase()
  return value === 'true' || value === '1'
}

/**
 * 构建 WMS GetMap 图层 URL（Leaflet L.tileLayer.wms 格式）
 * 返回基础 URL，Leaflet 会自动追加 bbox/width/height 等参数
 */
export function getWmsBaseUrl() {
  if (!isEnabled()) return null
  const base = getBaseUrl()
  const workspace = getWorkspace()
  return `${base}/${workspace}/wms`
}

/**
 * 构建 WMS 图层参数
 */
export function getWmsLayerOptions() {
  const workspace = getWorkspace()
  const layer = getLayer()
  return {
    layers: `${workspace}:${layer}`,
    format: 'image/png',
    transparent: true,
    version: '1.1.0',
    srs: 'EPSG:4326'
  }
}

/**
 * 通过后端代理获取 WFS 数据（避免浏览器跨域）
 */
export async function fetchWfsFeatures() {
  if (!isEnabled()) {
    return {
      ok: false,
      data: EMPTY_FEATURE_COLLECTION,
      message: 'GeoServer 未启用，当前使用本地数据。'
    }
  }

  try {
    const response = await api.get(
      `${import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000'}/api/geoserver/wfs`,
      { params: { layer: getLayer() } }
    )
    return {
      ok: true,
      data: response.data?.features ? response.data : EMPTY_FEATURE_COLLECTION,
      message: '已通过 GeoServer WFS 获取数据。'
    }
  } catch {
    return {
      ok: false,
      data: EMPTY_FEATURE_COLLECTION,
      message: 'GeoServer WFS 暂不可用，当前使用本地数据展示。'
    }
  }
}

/**
 * 检查 GeoServer 状态（通过后端代理）
 */
export async function checkGeoServerStatus() {
  if (!isEnabled()) {
    return { ok: false, reachable: false, message: 'GeoServer 未启用。' }
  }

  try {
    const response = await api.get(
      `${import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000'}/api/geoserver/status`
    )
    return response.data
  } catch {
    return { ok: false, reachable: false, message: '无法连接 GeoServer 状态接口。' }
  }
}
