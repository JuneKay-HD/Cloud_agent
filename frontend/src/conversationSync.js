// Merge an in-flight local turn with a possibly older server history snapshot.
// The response still comes from /api/chat; this function never generates facts.
function sameMessage(a, b) {
  if (!a || !b || a.role !== b.role) return false;
  if (a.result?.run_id && b.result?.run_id) return a.result.run_id === b.result.run_id;
  return (a.id && a.id === b.id) || a.text === b.text;
}

export function mergeRequestMessages(serverMessages, request) {
  const messages = [...serverMessages];
  if (!request || request.cancelled) return messages;
  const answer = request.assistantMessage;
  if (answer?.result?.run_id && messages.some(m => m.result?.run_id === answer.result.run_id)) return messages;
  const base = request.baseMessages;
  // A different history (for example another tab's turn) must stay authoritative.
  if (!messages.slice(0, base.length).every((message, i) => sameMessage(message, base[i]))) return messages;
  if (messages.length < base.length) messages.push(...base.slice(messages.length));
  if (messages.length === base.length) messages.push(request.userMessage);
  if (!sameMessage(messages[base.length], request.userMessage)) return messages;
  if (answer && messages.length === base.length + 1) messages.push(answer);
  return messages;
}
