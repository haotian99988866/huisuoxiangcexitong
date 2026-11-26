export function telegramDownload(tg, url, fileName) {
  if (!tg?.downloadFile || !tg.isVersionAtLeast?.('8.0')) {
    throw new Error('当前 Telegram 版本不支持下载，请更新 Telegram 后重试')
  }
  if (!url.startsWith('https://')) {
    throw new Error('Telegram 下载需要使用 HTTPS 小程序入口')
  }
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Telegram 未响应下载请求，请重新导出')), 30000)
    try {
      tg.downloadFile({ url, file_name: fileName }, accepted => {
        clearTimeout(timer)
        if (accepted) resolve()
        else reject(new Error('未确认下载，请重新导出'))
      })
    } catch (error) {
      clearTimeout(timer)
      reject(error)
    }
  })
}
