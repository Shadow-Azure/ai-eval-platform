/** Copy plain text to the clipboard; rejects when the browser has no async clipboard. */
export function copyText(value: string): Promise<void> {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(value);
  return Promise.reject(new Error("当前浏览器不支持剪贴板写入"));
}
