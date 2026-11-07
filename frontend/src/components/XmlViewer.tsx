import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { EditorState, RangeSetBuilder, StateEffect, StateField, type Extension } from "@codemirror/state";
import { Decoration, EditorView, drawSelection, keymap, lineNumbers, type DecorationSet } from "@codemirror/view";
import { defaultKeymap } from "@codemirror/commands";
import { xml } from "@codemirror/lang-xml";
import {
  HighlightStyle,
  codeFolding,
  foldGutter,
  foldKeymap,
  foldedRanges,
  syntaxHighlighting,
  unfoldEffect,
} from "@codemirror/language";
import { highlightSelectionMatches, search, searchKeymap } from "@codemirror/search";
import { tags } from "@lezer/highlight";
import type { Evidence } from "../types";

export type XmlViewerHandle = {
  revealEvidence: (ev: Evidence[]) => void;
  clearHighlight: () => void;
  revealRange: (start: number, end: number) => void;
  revealLine: (line: number) => void;
};

type Props = {
  text: string;
  label: string;
  /** Вызывается, когда подсветка появилась или была снята. */
  onHighlightChange?: (active: boolean) => void;
};

type Range = { from: number; to: number };

const setHighlight = StateEffect.define<Range[]>();

const MAX_DECORATED_LINES = 20000;
const lineDeco = Decoration.line({ class: "cm-evidence-line" });
const markDeco = Decoration.mark({ class: "cm-evidence-mark" });

const highlightField = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(value, tr) {
    for (const e of tr.effects) {
      if (e.is(setHighlight)) {
        const ranges = e.value;
        if (ranges.length === 0) return Decoration.none;
        const doc = tr.state.doc;
        const lineStarts = new Set<number>();
        const items: { from: number; to: number; deco: Decoration }[] = [];
        let budget = MAX_DECORATED_LINES;
        for (const r of ranges) {
          const first = doc.lineAt(r.from);
          const last = doc.lineAt(Math.max(r.from, r.to));
          for (let n = first.number; n <= last.number && budget > 0; n++) {
            const line = doc.line(n);
            if (lineStarts.has(line.from)) continue;
            lineStarts.add(line.from);
            items.push({ from: line.from, to: line.from, deco: lineDeco });
            budget--;
          }
          if (r.to > r.from) items.push({ from: r.from, to: r.to, deco: markDeco });
        }
        items.sort((a, b) => a.from - b.from || a.deco.startSide - b.deco.startSide);
        const builder = new RangeSetBuilder<Decoration>();
        for (const it of items) builder.add(it.from, it.to, it.deco);
        return builder.finish();
      }
    }
    return value.map(tr.changes);
  },
  provide: (f) => EditorView.decorations.from(f),
});

const highlightStyle = HighlightStyle.define([
  { tag: tags.tagName, color: "var(--color-ink)", fontWeight: "500" },
  { tag: tags.angleBracket, color: "var(--color-muted)" },
  { tag: tags.attributeName, color: "var(--color-ink-2)" },
  { tag: tags.attributeValue, color: "var(--color-muted)" },
  { tag: [tags.comment, tags.blockComment], color: "var(--color-muted)", fontStyle: "italic" },
  { tag: [tags.processingInstruction, tags.documentMeta], color: "var(--color-muted)" },
  { tag: tags.content, color: "var(--color-ink)" },
  { tag: [tags.invalid], color: "var(--color-error)" },
]);

const theme = EditorView.theme({
  "&": {
    height: "100%",
    color: "var(--color-ink)",
    backgroundColor: "var(--color-plate)",
    fontSize: "var(--text-sm)",
  },
  ".cm-scroller": { fontFamily: "var(--font-outlier)", lineHeight: "1.6", overflow: "auto" },
  ".cm-content": { caretColor: "var(--color-accent)" },
  "&.cm-focused": { outline: "2px solid var(--color-focus)", outlineOffset: "-2px" },
  ".cm-gutters": {
    backgroundColor: "var(--color-paper)",
    color: "var(--color-muted)",
    borderRight: "var(--rule-hair) solid var(--color-rule)",
  },
  ".cm-lineNumbers .cm-gutterElement": { padding: "0 var(--space-xs) 0 var(--space-sm)", fontVariantNumeric: "tabular-nums" },
  ".cm-foldGutter .cm-gutterElement": { cursor: "pointer", padding: "0 var(--space-2xs)" },
  ".cm-foldPlaceholder": {
    backgroundColor: "var(--color-paper-2)",
    border: "var(--rule-hair) solid var(--color-rule)",
    color: "var(--color-muted)",
    borderRadius: "0",
    padding: "0 var(--space-2xs)",
  },
  ".cm-evidence-line": { backgroundColor: "var(--color-accent-soft)" },
  ".cm-evidence-mark": {
    backgroundColor: "color-mix(in oklab, var(--color-accent) 22%, transparent)",
    boxShadow: "0 -1px 0 var(--color-accent), 0 1px 0 var(--color-accent)",
  },
  ".cm-selectionBackground, &.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground": {
    backgroundColor: "color-mix(in oklab, var(--color-accent) 25%, transparent)",
  },
  ".cm-selectionMatch": { backgroundColor: "var(--color-paper-3)" },
  ".cm-searchMatch": { backgroundColor: "var(--color-paper-3)", outline: "var(--rule-hair) solid var(--color-ink)" },
  ".cm-searchMatch-selected": { backgroundColor: "var(--color-accent-soft)", outline: "2px solid var(--color-accent)" },
  ".cm-panels": {
    backgroundColor: "var(--color-paper-2)",
    color: "var(--color-ink)",
    borderBottom: "var(--rule-bar) solid var(--color-ink)",
    fontFamily: "var(--font-body)",
  },
  ".cm-panels input, .cm-panels button, .cm-panels label": { fontSize: "var(--text-sm)" },
  ".cm-textfield": {
    minHeight: "32px",
    backgroundColor: "var(--color-plate)",
    border: "var(--rule-hair) solid var(--color-ink)",
    borderRadius: "0",
    padding: "0 var(--space-xs)",
    color: "var(--color-ink)",
  },
  ".cm-button": {
    minHeight: "32px",
    backgroundImage: "none",
    backgroundColor: "var(--color-plate)",
    border: "var(--rule-hair) solid var(--color-ink)",
    borderRadius: "0",
    color: "var(--color-ink)",
    padding: "0 var(--space-sm)",
    cursor: "pointer",
  },
  ".cm-button:hover": { backgroundColor: "var(--color-paper-3)" },
  ".cm-panel.cm-search [name=close]": { color: "var(--color-ink)", cursor: "pointer", fontSize: "var(--text-lg)" },
  ".cm-tooltip": { backgroundColor: "var(--color-plate)", border: "var(--rule-bar) solid var(--color-ink)", borderRadius: "0" },
});

const phrases = EditorState.phrases.of({
  Find: "Найти",
  Replace: "Заменить",
  next: "далее",
  previous: "назад",
  all: "все",
  "match case": "с учётом регистра",
  regexp: "регулярное выражение",
  "by word": "слово целиком",
  replace: "заменить",
  "replace all": "заменить все",
  close: "закрыть",
  "Go to line": "Перейти к строке",
  go: "перейти",
  "Folded lines": "Свёрнутые строки",
  "Unfolded lines": "Развёрнутые строки",
  to: "по",
  "folded code": "свёрнутый код",
  unfold: "развернуть",
  "Fold line": "Свернуть",
  "Unfold line": "Развернуть",
  "current match": "текущее совпадение",
  "on line": "в строке",
  "replaced match on line $": "заменено в строке $",
  "replaced $ matches": "заменено совпадений: $",
  "Selection deleted": "Выделение удалено",
});

function baseExtensions(): Extension[] {
  return [
    EditorState.readOnly.of(true),
    EditorView.contentAttributes.of({ "aria-readonly": "true", "aria-label": "Текст XML-файла" }),
    phrases,
    lineNumbers(),
    codeFolding(),
    foldGutter(),
    drawSelection(),
    xml(),
    syntaxHighlighting(highlightStyle),
    search({ top: true }),
    highlightSelectionMatches(),
    keymap.of([...searchKeymap, ...foldKeymap, ...defaultKeymap]),
    highlightField,
    theme,
  ];
}

const XmlViewer = forwardRef<XmlViewerHandle, Props>(function XmlViewer({ text, label, onHighlightChange }, ref) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const notify = useRef(onHighlightChange);
  notify.current = onHighlightChange;

  useEffect(() => {
    if (!host.current) return;
    const v = new EditorView({
      parent: host.current,
      state: EditorState.create({ doc: text, extensions: baseExtensions() }),
    });
    view.current = v;
    return () => {
      v.destroy();
      view.current = null;
    };
    // Редактор создаётся один раз; текст меняется отдельным эффектом.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const v = view.current;
    if (!v || v.state.doc.toString() === text) return;
    v.setState(EditorState.create({ doc: text, extensions: baseExtensions() }));
    notify.current?.(false);
  }, [text]);

  useImperativeHandle(
    ref,
    () => {
      const reveal = (ranges: Range[]) => {
        const v = view.current;
        if (!v) return;
        const len = v.state.doc.length;
        const clean = ranges
          .map((r) => ({ from: Math.max(0, Math.min(r.from, len)), to: Math.max(0, Math.min(r.to, len)) }))
          .filter((r) => r.to >= r.from);
        if (clean.length === 0) return;
        const unfold: { from: number; to: number }[] = [];
        foldedRanges(v.state).between(0, len, (from, to) => {
          if (clean.some((r) => r.from < to && r.to > from)) unfold.push({ from, to });
        });
        const first = clean[0];
        v.dispatch({
          effects: [
            ...unfold.map((u) => unfoldEffect.of(u)),
            setHighlight.of(clean),
            EditorView.scrollIntoView(first.from, { y: "center", x: "nearest" }),
          ],
          selection: { anchor: first.from, head: first.to },
        });
        notify.current?.(true);
      };
      return {
        revealEvidence: (ev) => reveal(ev.map((e) => ({ from: e.start, to: e.end }))),
        revealRange: (start, end) => reveal([{ from: start, to: end }]),
        revealLine: (line) => {
          const v = view.current;
          if (!v) return;
          const n = Math.max(1, Math.min(line, v.state.doc.lines));
          const l = v.state.doc.line(n);
          reveal([{ from: l.from, to: l.to }]);
        },
        clearHighlight: () => {
          view.current?.dispatch({ effects: setHighlight.of([]) });
          notify.current?.(false);
        },
      };
    },
    [],
  );

  return <div className="xml-viewer" ref={host} role="region" aria-label={label} />;
});

export default XmlViewer;
