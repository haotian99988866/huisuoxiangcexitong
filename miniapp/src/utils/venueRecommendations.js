import { API_BASE_URL } from './twa'
import { tenantHeaders } from './tenant'
import { requestAdmin } from './admin'
import { imageDraft } from './hotelDraft'

export const recommendationKinds = { activity: '会所活动', newcomer: '新人推荐' }
export const recommendationDraft = (key, operation, value) =>
  imageDraft('venue-recommendation-pending', key, operation, value)

export async function requestRecommendations(venueId, signal) {
  const response = await fetch(`${API_BASE_URL}/webhook`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...tenantHeaders() },
    body: JSON.stringify({ action: 'get_venue_recommendations', venue_id: venueId }),
    cache: 'no-store', signal,
  })
  const result = await response.json()
  if (!response.ok) throw new Error(result.error || '会所推荐读取失败')
  return result
}

export const saveRecommendation = (payload, file) => requestAdmin('admin_venue_recommendation_save', payload, {
  uploadPath: '/admin-venue-recommendation-save', file, fileField: 'image', timeoutMs: 120000,
  timeoutMessage: '推荐保存结果未确认，请使用原操作编号重试',
})
