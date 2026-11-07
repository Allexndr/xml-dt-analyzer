import type { Evidence, ToolResult } from "../types";
import { isRecord, scalar } from "../format";
import EvidenceList from "./EvidenceList";

type Props = {
  title: string;
  result: ToolResult;
  onReveal: (ev: Evidence[]) => void;
};

function asArray(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

function numOrDash(v: unknown): string {
  return typeof v === "number" ? new Intl.NumberFormat("ru-RU").format(v) : "не указано";
}

function Warnings({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <ul className="notes" aria-label="Предупреждения">
      {items.map((w, i) => (
        <li key={i} className="note">
          {w}
        </li>
      ))}
    </ul>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <dt>{label}</dt>
      <dd className="num code">{value}</dd>
    </div>
  );
}

function Goods({ value }: { value: Record<string, unknown> }) {
  const count = value.goods_items;
  const declared = value.declared_goods_quantity;
  return (
    <dl className="stats">
      <Stat label="Товарных позиций в файле" value={numOrDash(count)} />
      <Stat label="Заявлено в графе 5" value={numOrDash(declared)} />
    </dl>
  );
}

function IdList({ label, ids }: { label: string; ids: unknown[] }) {
  return (
    <div className="idlist">
      <dt>{label}</dt>
      <dd className="code">{ids.length ? ids.map(scalar).join(", ") : "нет"}</dd>
    </div>
  );
}

function Marking({ value }: { value: Record<string, unknown> }) {
  const withList = asArray(value.items_with_marking_data_list);
  const withoutList = asArray(value.items_without_marking_data_list);
  const dups = isRecord(value.duplicate_codes) ? Object.entries(value.duplicate_codes) : [];
  const perItem = asArray(value.per_item).filter(isRecord);
  const note = typeof value.note === "string" ? value.note : null;
  return (
    <>
      <dl className="stats">
        <Stat label="Товарных позиций" value={numOrDash(value.goods_items)} />
        <Stat label="С данными о маркировке" value={numOrDash(value.items_with_marking_data)} />
        <Stat label="Кодов перечислено" value={numOrDash(value.codes_listed)} />
        <Stat label="Уникальных кодов" value={numOrDash(value.codes_listed_unique)} />
        <Stat label="Кодов в диапазонах" value={numOrDash(value.codes_in_ranges)} />
        <Stat label="Диапазонов с неизвестным размером" value={numOrDash(value.ranges_with_unknown_size)} />
        <Stat label="Агрегированных идентификаторов" value={numOrDash(value.aggregated_customs_ids)} />
      </dl>
      <dl className="idlists">
        <IdList label="Товары с данными о маркировке" ids={withList} />
        <IdList label="Товары без данных о маркировке" ids={withoutList} />
      </dl>
      {dups.length > 0 && (
        <section aria-label="Повторяющиеся коды">
          <h4 className="sources__title">Повторяющиеся коды</h4>
          <ul className="plain-list">
            {dups.map(([code, n]) => (
              <li key={code}>
                <span className="code">{code}</span>, повторов: <span className="num code">{scalar(n)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {perItem.length > 0 && (
        <section aria-label="Маркировка по товарам">
          <h4 className="sources__title">По товарам</h4>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Таблица маркировки по товарам">
            <table className="table table--dense">
              <thead>
                <tr>
                  <th scope="col">Товар</th>
                  <th scope="col">Признак</th>
                  <th scope="col" className="num">Заявлено</th>
                  <th scope="col" className="num">Перечислено</th>
                  <th scope="col" className="num">Уникальных</th>
                  <th scope="col" className="num">Диапазонов</th>
                  <th scope="col" className="num">Агрег. ID</th>
                </tr>
              </thead>
              <tbody>
                {perItem.map((r, i) => (
                  <tr key={i}>
                    <th scope="row" className="code">{scalar(r.goods_no)}</th>
                    <td className="code">{scalar(r.marking_sign) || "нет"}</td>
                    <td className="num code">{r.declared_quantity == null ? "нет" : scalar(r.declared_quantity)}</td>
                    <td className="num code">{scalar(r.listed_codes)}</td>
                    <td className="num code">{scalar(r.unique_codes)}</td>
                    <td className="num code">{asArray(r.ranges).length}</td>
                    <td className="num code">{scalar(r.aggregated_ids)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {note && <p className="muted">{note}</p>}
    </>
  );
}

function Concept({ value }: { value: Record<string, unknown> }) {
  const found = typeof value.found === "number" ? value.found : 0;
  const title = scalar(value.title);
  const graphs = asArray(value.graphs).map(scalar);
  const values = asArray(value.values).filter(isRecord);
  return (
    <>
      {graphs.length > 0 && (
        <p className="muted">{graphs.length > 1 ? "Графы" : "Графа"}: <span className="code">{graphs.join(", ")}</span></p>
      )}
      {found === 0 ? (
        <p className="empty-note">В файле нет сведений: {title}.</p>
      ) : (
        <ol className="values">
          {values.map((v, i) => {
            const fields = asArray(v.fields).filter(isRecord);
            return (
              <li key={i} className="value">
                <p className="value__head">
                  {v.goods_no ? <>Товар № <span className="num code">{scalar(v.goods_no)}</span></> : <>Значение <span className="num code">{i + 1}</span></>}
                  {" "}
                  <span className="muted code">{scalar(v.element)}</span>
                </p>
                {fields.length === 0 ? (
                  <p className="muted">Вложенных полей нет.</p>
                ) : (
                  <dl className="fields">
                    {fields.map((f, j) => {
                      const attrs = isRecord(f.attrs) ? Object.entries(f.attrs) : [];
                      return (
                        <div key={j} className="fields__row">
                          <dt className="code">{scalar(f.element)}</dt>
                          <dd>
                            <span className="code">{scalar(f.value)}</span>
                            {attrs.length > 0 && (
                              <span className="muted code">
                                {" "}
                                {attrs.map(([k, a]) => `${k}="${scalar(a)}"`).join(" ")}
                              </span>
                            )}
                          </dd>
                        </div>
                      );
                    })}
                  </dl>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </>
  );
}

export default function ResultView({ title, result, onReveal }: Props) {
  const v = result.value;
  const body =
    result.tool === "count_goods" ? (
      <Goods value={v} />
    ) : result.tool === "marking_stats" ? (
      <Marking value={v} />
    ) : result.tool === "get_concept" ? (
      <Concept value={v} />
    ) : (
      <pre className="raw code">{JSON.stringify(v, null, 2)}</pre>
    );
  return (
    <article className="result" aria-label={`Результат: ${title}`}>
      <h3>{title}</h3>
      {body}
      <Warnings items={result.warnings} />
      <EvidenceList evidence={result.evidence} onReveal={onReveal} />
    </article>
  );
}
