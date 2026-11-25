import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

export default function KnowledgeMarkdown({ text }: { text: string }) {
  return (
    <div className="knowledge-markdown">
      <Markdown skipHtml disallowedElements={["img"]} remarkPlugins={[remarkGfm]} components={{
        table: ({ node: _node, ...props }) => <div className="knowledge-table-scroll"><table {...props} /></div>,
      }}>
        {text}
      </Markdown>
    </div>
  );
}
