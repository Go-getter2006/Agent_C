// 小题里可以直接改的代码区（CodeMirror 6）。整段可改，不提示错在哪一行。
import { useEffect, useRef } from "react";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { cpp } from "@codemirror/lang-cpp";
import { HighlightStyle, indentUnit, syntaxHighlighting } from "@codemirror/language";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap, lineNumbers } from "@codemirror/view";
import { tags } from "@lezer/highlight";

// 只用两三种低饱和度的颜色，不抢被指出那一行的底色
const highlight = HighlightStyle.define([
  { tag: [tags.keyword, tags.typeName], fontWeight: "600" },
  { tag: [tags.string, tags.number, tags.character], color: "#3d5a73" },
  { tag: tags.comment, color: "#55616e" },
]);

const theme = EditorView.theme({
  "&": { backgroundColor: "var(--surface)", color: "var(--ink)", fontSize: "var(--code-fs)" },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": { fontFamily: "var(--font-code)", lineHeight: "var(--code-lh)" },
  ".cm-content": { padding: "0", caretColor: "var(--ink)" },
  ".cm-line": { padding: "0 12px 0 0" },
  ".cm-gutters": { backgroundColor: "var(--surface)", color: "#636a77", border: "0" },
  ".cm-lineNumbers .cm-gutterElement": { minWidth: "var(--lineno-w)", padding: "0 12px 0 0", textAlign: "right" },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": { backgroundColor: "#dadee1 !important" },
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
