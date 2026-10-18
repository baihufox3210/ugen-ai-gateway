function Markdown({ text }: { text: string }) {
  const inline = (value: string) => value.split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)]+\))/g).map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith('*') && part.endsWith('*')) return <em key={i}>{part.slice(1, -1)}</em>;
    if (part.startsWith('`') && part.endsWith('`')) return <code key={i}>{part.slice(1, -1)}</code>;
    const link = part.match(/^\[([^\]]+)\]\((https?:\/\/[^)]+)\)$/);
    if (link) return <a key={i} href={link[2]} target="_blank" rel="noreferrer">{link[1]}</a>;
    return part;
  });
  return <div className="markdown">{text.split(/\n\s*\n/).map((block, i) => {
    const lines = block.split('\n');
    if (lines.every(line => /^\s*[-*+]\s+/.test(line))) return <ul key={i}>{lines.map((line, j) => <li key={j}>{inline(line.replace(/^\s*[-*+]\s+/, ''))}</li>)}</ul>;
    if (lines.every(line => /^\s*\d+[.)]\s+/.test(line))) return <ol key={i}>{lines.map((line, j) => <li key={j}>{inline(line.replace(/^\s*\d+[.)]\s+/, ''))}</li>)}</ol>;
    if (block.startsWith('```')) return <pre key={i}><code>{block.replace(/^```[^\n]*\n?/, '').replace(/\n?```$/, '')}</code></pre>;
    const heading = lines.length === 1 && lines[0].match(/^(#{1,4})\s+(.+)$/);
    if (heading) { const Tag = `h${heading[1].length + 1}` as 'h2' | 'h3' | 'h4' | 'h5'; return <Tag key={i}>{inline(heading[2])}</Tag>; }
    return <p key={i}>{lines.map((line, j) => <span key={j}>{j > 0 && <br/>}{inline(line)}</span>)}</p>;
  })}</div>;
}
export default Markdown;
