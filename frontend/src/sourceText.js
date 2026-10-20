// Reflow PDF soft line wraps for display only; source strings and offsets stay intact.
export function flowSourceText(value) {
  const lines = String(value || '').split('\n');
  const output = [], prose = [];
  let fence = '', math = '';
  const flush = () => {
    if (prose.length) output.push(prose.join('\n').replace(/([A-Za-z])-\n(?=[A-Za-z])/g, '$1').replace(/\n/g, ' '));
    prose.length = 0;
  };
  for (const line of lines) {
    const trimmed = line.trim();
    if (/^(?:```|~~~)/.test(trimmed)) {
      flush(); output.push(line);
      const marker = trimmed.slice(0, 3);
      fence = fence === marker ? '' : fence || marker;
    } else if (fence) output.push(line);
    else if (math) { output.push(line); if (trimmed.endsWith(math)) math = ''; }
    else if (/^(?:\$\$|\\\[)/.test(trimmed)) {
      flush(); output.push(line);
      const end = trimmed.startsWith('$$') ? '$$' : '\\]';
      if (!trimmed.slice(2).endsWith(end)) math = end;
    } else if (!trimmed || /^(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|>|\||公式[：:])/.test(trimmed) || line.includes('|')) {
      flush(); output.push(line);
    } else prose.push(trimmed);
  }
  flush();
  return output.join('\n');
}
