import Markdown, { type Components, defaultUrlTransform } from 'react-markdown';
import remarkGfm from 'remark-gfm';

const components: Components = {
  h1: ({ children }) => <h3>{children}</h3>,
  h2: ({ children }) => <h3>{children}</h3>,
  table: ({ children }) => (
    <div className="advisor-table-scroll" role="region" aria-label="Advisor data table" tabIndex={0}>
      <table>{children}</table>
    </div>
  ),
  th: ({ children, style }) => <th scope="col" style={style}>{children}</th>,
  a: ({ children, href }) => href
    ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
    : <span>{children}</span>,
};

export function AdvisorMarkdown({ children, className = '', live = false }: {
  children: string;
  className?: string;
  live?: boolean;
}) {
  return (
    <div className={`advisor-markdown ${className}`} aria-live={live ? 'polite' : undefined}>
      <Markdown
        remarkPlugins={[remarkGfm]}
        components={components}
        skipHtml
        disallowedElements={['img']}
        // Model output may contain links; only explicit web URLs or local anchors are navigable.
        urlTransform={url => /^(https?:\/\/|#)/i.test(url) ? defaultUrlTransform(url) : ''}
      >{children}</Markdown>
    </div>
  );
}
