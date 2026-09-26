const START = "\u0001";
const END = "\u0002";

/**
 * A search match from the RAG API: the matched words sit between \x01 and \x02 (control
 * characters, so they never occur in the text). Rendered as text with <mark>s; React escapes it.
 */
export function Highlighted({ text }: { text: string }) {
  const [first, ...rest] = text.split(START);
  return (
    <>
      {first}
      {rest.map((chunk, i) => {
        const [marked, after = ""] = chunk.split(END);
        return (
          <span key={i}>
            <mark>{marked}</mark>
            {after}
          </span>
        );
      })}
    </>
  );
}
