// 小题里可以直接改的代码区（CodeMirror 6）。整段可改，不提示错在哪一行。
import { useEffect, useRef } from "react";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { cpp } from "@codemirror/lang-cpp";
import { HighlightStyle, indentUnit, syntaxHighlighting } from "@codemirror/language";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap, lineNumbers } from "@codemirror/view";
import { tags } from "@lezer/highlight";

// 和只读的代码块（lib/cSyntax.ts）同一套颜色：tokens.css 的 --syn-*
const highlight = HighlightStyle.define([
  { tag: [tags.keyword, tags.controlKeyword, tags.definitionKeyword, tags.modifier, tags.operatorKeyword], color: "var(--syn-kw)" },
  { tag: [tags.typeName, tags.standard(tags.typeName)], color: "var(--syn-type)" },
  { tag: [tags.function(tags.variableName), tags.function(tags.propertyName)], color: "var(--syn-fn)" },
  { tag: [tags.string, tags.character], color: "var(--syn-str)" },
  { tag: tags.escape, color: "var(--syn-esc)" },
  { tag: [tags.number, tags.bool, tags.null, tags.atom], color: "var(--syn-num)" },
  { tag: [tags.comment, tags.lineComment, tags.blockComment], color: "var(--syn-com)", fontStyle: "italic" },
  { tag: [tags.processingInstruction, tags.meta], color: "var(--syn-pre)" },
]);

const theme = EditorView.theme({
  "&": { backgroundColor: "var(--surface)", color: "var(--ink)", fontSize: "var(--code-fs)" },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": { fontFamily: "var(--font-code)", lineHeight: "var(--code-lh)" },
  ".cm-content": { padding: "0", caretColor: "var(--ink)" },
  ".cm-line": { padding: "0 12px 0 0" },
  ".cm-gutters": { backgroundColor: "var(--surface)", color: "var(--lineno)", border: "0" },
  ".cm-lineNumbers .cm-gutterElement": { minWidth: "var(--lineno-w)", padding: "0 12px 0 0", textAlign: "right" },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": { backgroundColor: "var(--surface-3) !important" },
  ".cm-cursor": { borderLeftColor: "var(--ink)", borderLeftWidth: "2px" },
});

interface Props {
  value: string;
  onChange: (value: string) => void;
  label: string;
  disabled?: boolean;
  start?: number; // 第一行显示的行号：摘录的代码和原来的行号对得上
}

export default function CodeEditor({ value, onChange, label, disabled = false, start = 1 }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView>();
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    if (!host.current) return;
    const state = EditorState.create({
      doc: value,
      extensions: [
        lineNumbers({ formatNumber: (n) => String(n + start - 1) }),
        history(),
        keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab]),
        indentUnit.of("    "),
        EditorState.tabSize.of(4),
        cpp(),
        syntaxHighlighting(highlight),
        theme,
        EditorView.contentAttributes.of({ "aria-label": label, spellcheck: "false", autocapitalize: "off" }),
        EditorView.editable.of(!disabled),
        EditorView.updateListener.of((u) => {
          if (u.docChanged) onChangeRef.current(u.state.doc.toString());
        }),
      ],
    });
    view.current = new EditorView({ state, parent: host.current });
    return () => view.current?.destroy();
    // 只在挂载时建一次；之后内容由编辑器自己管
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [disabled, start]);

  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== value) {
      v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
    }
  }, [value]);

  return <div ref={host} className="editor" />;
}
