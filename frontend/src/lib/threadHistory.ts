import type { Message } from "../types";

const EMPTY: Message[] = [];

/** undefined 表示作业列表尚未返回消息信息，0 表示服务器确认这一题没有消息。 */
export function threadHistory(snapshot: Message[] | null | undefined, lastMessageId?: number, latestNotice?: number) {
  const expectedId = Math.max(latestNotice ?? 0, lastMessageId ?? 0);
  const behind = expectedId > (snapshot?.[snapshot.length - 1]?.id ?? 0);
  const confirmedEmpty = lastMessageId === 0 && !expectedId && !snapshot?.length;
  let messages = snapshot ?? null;
  if (confirmedEmpty) messages = EMPTY;
  else if (behind && !snapshot?.length) messages = null;
  return { messages, expectedId, behind, confirmedEmpty };
}
