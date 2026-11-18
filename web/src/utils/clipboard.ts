/**
 * Copy plain text to the clipboard.
 *
 * Production is also served over plain HTTP, where `navigator.clipboard` is
 * unavailable because it requires a secure context. Fall back to the legacy
 * textarea + execCommand path so the console copy button works there too.
 */
export async function copyText(value: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch {
      // Permission denied or a non-secure context: use the legacy fallback.
    }
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.top = "-1000px";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) throw new Error("复制失败");
}
