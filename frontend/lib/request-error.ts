/** Recovery guidance only: never automatically replay model requests. */
export function requestError(status: number, detail?: string, retryAfter?: string | null, context: "agent" | "rag" = "agent"): string {
  const seconds = Number(retryAfter);
  const wait = retryAfter && Number.isFinite(seconds) && seconds > 0 ? `请至少等待 ${Math.ceil(seconds)} 秒。` : "";
  if (status === 429 && context === "rag") return `问答请求额度或上游请求频率受限。${wait}两种检索共用每个网络地址每天 5 次、全局每月 300 次请求额度（UTC）。刷新不会恢复次数，已发送的失败请求也计数；不会自动重试。`;
  if (status === 429) return `体验次数、请求频率或费用额度受限。${wait}刷新不会恢复额度；请稍后再试。Agent 的次数按网络地址共享，每日额度按 UTC 重置，月度费用额度需等下月或由维护者处理。`;
  if (status === 409) return "运行仍在执行、计划或来源已变化，或执行后校验未通过。请先刷新已保存的运行并核对来源和进度，不要重复创建。";
  if (status === 401) return "访客会话已失效，请刷新页面重新连接。更换浏览器或清除 Cookie 后，无法找回原匿名计划。";
  if (status >= 500) return "连接超时或服务暂不可用，尚不能确认操作结果。Agent 请先刷新已保存的运行；问答请先检查是否收到回答。不会自动重试，避免重复调用和计费。";
  return detail || "请求未完成，请检查输入后再试。";
}
