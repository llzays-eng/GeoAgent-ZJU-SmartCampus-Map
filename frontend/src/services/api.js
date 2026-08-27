import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000',
  timeout: 10000
})

export function getHealth() {
  return api.get('/api/health')
}

export function getStudyRooms() {
  return api.get('/api/study-rooms')
}

export function getPois() {
  return api.get('/api/pois')
}

export function getBuildings() {
  return api.get('/api/buildings')
}

export function getConfig() {
  return api.get('/api/config')
}

export function getChargerStatus() {
  return api.get('/api/chargers/status')
}

export function getChargerStations() {
  return api.get('/api/chargers/stations')
}

export function recommendStudyRoom(query) {
  return api.post('/api/ai/recommend-study-room', { query })
}
