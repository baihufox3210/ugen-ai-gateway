import ReactMarkdown, { type Components } from 'react-markdown';
import rehypeKatex from 'rehype-katex';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import 'katex/dist/katex.min.css';

// Code is left exactly as written: fenced blocks (closed or still streaming) and inline spans.
const CODE = /(```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]*`)/g;
// Kept as is: an escaped \$, a $$…$$ block, or $…$ math in Pandoc's sense (no space just
// inside the dollars, no digit right after). Any other lone $ is a currency sign.
const DOLLARS = /\\\$|\$\$[\s\S]*?\$\$|\$(?=\S)(?:[^$\n\\]|\\.)*?(?<=\S)\$(?!\d)|\$/g;

/** Accept the \( \) and \[ \] delimiters models often use, and keep prices out of math. */
export function normalizeMath(text: string): string {
  return text.split(CODE).map((part, index) => {
    if (index % 2 === 1) return part;  // a code segment
    return part
      .replace(/\\\[([\s\S]+?)\\\]/g, (_, math: string) => `\n$$\n${math.trim()}\n$$\n`)
      .replace(/\\\((.+?)\\\)/g, (_, math: string) => `$${math.trim()}$`)
      .replace(DOLLARS, match => match === '$' ? '\\$' : match);
  }).join('');
}

const components: Components = {
  // Headings start one level down so a reply's "#" fits under the panel's own title.
  h1: ({ node: _node, ...props }) => <h2 {...props}/>,
  h2: ({ node: _node, ...props }) => <h3 {...props}/>,
  h3: ({ node: _node, ...props }) => <h4 {...props}/>,
  h4: ({ node: _node, ...props }) => <h5 {...props}/>,
  h5: ({ node: _node, ...props }) => <h6 {...props}/>,
  a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer noopener"/>,
  // Wide tables scroll inside the message instead of stretching it.
  table: ({ node: _node, ...props }) => <div className="table-wrap"><table {...props}/></div>,
  // Never fetch images named in model output; that would reveal the reader to any URL.
  img: ({ src, alt }) => typeof src === 'string' && src
    ? <a href={src} target="_blank" rel="noreferrer noopener">{alt || 'Image'}</a>
    : <>{alt}</>,
};

function Markdown({ text }: { text: string }) {
  return <div className="markdown">
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: 'ignore' }]]}
      components={components}>
      {normalizeMath(text)}
    </ReactMarkdown>
  </div>;
}

export default Markdown;
