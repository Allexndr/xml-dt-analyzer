import { useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import type { CatalogEntry } from "../types";

type Props = {
  catalog: CatalogEntry[];
  value: string | null;
  onChange: (key: string) => void;
};

function graphLabel(c: CatalogEntry): string {
  if (c.graphs.length === 0) return "";
  return `${c.graphs.length > 1 ? "графы" : "графа"} ${c.graphs.join(", ")}`;
}

export default function ConceptPicker({ catalog, value, onChange }: Props) {
  const id = useId();
  const listId = `${id}-list`;
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  const selected = catalog.find((c) => c.key === value) ?? null;

  const options = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return catalog;
    return catalog.filter((c) => {
      const hay = [c.title, c.key, ...c.synonyms, ...c.graphs.map((g) => `графа ${g}`)].join(" ").toLowerCase();
      return hay.includes(q);
    });
  }, [catalog, query]);

  const choose = (c: CatalogEntry) => {
    onChange(c.key);
    setQuery("");
    setOpen(false);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setActive((a) => Math.min(a + 1, options.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter" && open && options[active]) {
      e.preventDefault();
      choose(options[active]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div
      className="combo"
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOpen(false);
      }}
    >
      <label className="field__label" htmlFor={`${id}-input`}>
        Понятие из каталога
      </label>
      <input
        id={`${id}-input`}
        ref={input}
        className="input"
        type="search"
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && options[active] ? `${id}-opt-${active}` : undefined}
        autoComplete="off"
        name="concept-search"
        placeholder={selected ? `${selected.title}` : "Начните вводить название или номер графы"}
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setActive(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
      />
      {open && (
        <ul id={listId} className="combo__list" role="listbox" aria-label="Понятия каталога">
          {options.length === 0 && <li className="combo__empty">Ничего не найдено.</li>}
          {options.map((c, i) => (
            <li
              key={c.key}
              id={`${id}-opt-${i}`}
              role="option"
              aria-selected={c.key === value}
              className={`combo__opt${i === active ? " is-active" : ""}${c.key === value ? " is-selected" : ""}`}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => choose(c)}
              onMouseEnter={() => setActive(i)}
            >
              <span className="combo__title">{c.title}</span>
              {graphLabel(c) && <span className="combo__graph">{graphLabel(c)}</span>}
            </li>
          ))}
        </ul>
      )}
      {selected && (
        <p className="field__hint">
          Выбрано: {selected.title}
          {graphLabel(selected) ? ` (${graphLabel(selected)})` : ""}
        </p>
      )}
    </div>
  );
}
