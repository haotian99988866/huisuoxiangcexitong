export const consumptionDate = value => value
  ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Phnom_Penh', hour12: false }) : '暂无'
