/**
 * A small renderer for the job descriptions the scrapers produce.
 *
 * They come back as the same Discord-flavoured markdown the bot posts: bold,
 * italics, links, bullet lists and headings, and nothing else. A markdown
 * library would be several times the size of this whole app for the four marks
 * we actually see, so this handles those and leaves anything unexpected as
 * plain text rather than mangling it.
 *
 * Everything is rendered as React elements, never dangerouslySetInnerHTML: the
 * text comes from a third-party job board and must not be able to inject markup.
 */

// **bold**, *italic*, `code`, [label](url) — ordered so bold wins over italic.
const INLINE = /(\*\*[^*]+\*\*|__[^_]+__|\*[^*\n]+\*|_[^_\n]+_|`[^`]+`|\[[^\]]+\]\([^)]+\))/g

function inline(text, keyPrefix) {
  const out = []
  let index = 0

  for (const piece of String(text).split(INLINE)) {
    if (!piece) continue
    const key = `${keyPrefix}-${index++}`

    if (/^(\*\*|__)/.test(piece) && piece.length > 4) {
      out.push(
        <strong key={key} className="font-semibold text-paper">
          {piece.slice(2, -2)}
        </strong>,
      )
    } else if (/^[*_]/.test(piece) && piece.length > 2) {
      out.push(<em key={key}>{piece.slice(1, -1)}</em>)
    } else if (piece.startsWith('`') && piece.length > 2) {
      out.push(
        <code key={key} className="rounded bg-ink-700 px-1 py-0.5 font-mono text-[0.95em]">
          {piece.slice(1, -1)}
        </code>,
      )
    } else if (piece.startsWith('[')) {
      const match = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(piece)
      if (match && /^https?:\/\//i.test(match[2])) {
        out.push(
          <a
            key={key}
            href={match[2]}
            target="_blank"
            rel="noopener noreferrer"
            className="text-prosple underline underline-offset-2 hover:text-paper"
          >
            {match[1]}
          </a>,
        )
      } else {
        // A non-http scheme (javascript:, data:) is shown as text, never linked.
        out.push(<span key={key}>{match ? match[1] : piece}</span>)
      }
    } else {
      out.push(<span key={key}>{piece}</span>)
    }
  }
  return out
}

/**
 * Render a markdown job description as a list of block elements.
 * @param {string} text
 */
export function Markdown({ text }) {
  if (!text) return null

  const blocks = []
  let list = null
  let key = 0

  const flushList = () => {
    if (!list) return
    blocks.push(
      <ul key={`ul-${key++}`} className="my-2.5 space-y-1.5 pl-1">
        {list.map((item, i) => (
          <li key={i} className="flex gap-2.5">
            <span className="mt-[0.55em] h-1 w-1 shrink-0 rounded-full bg-paper-faint" />
            <span>{inline(item, `li-${key}-${i}`)}</span>
          </li>
        ))}
      </ul>,
    )
    list = null
  }

  for (const raw of String(text).split('\n')) {
    const line = raw.trimEnd()

    if (!line.trim()) {
      flushList()
      continue
    }

    const bullet = /^\s*[-*•‣]\s+(.*)$/.exec(line)
    if (bullet) {
      list = list || []
      list.push(bullet[1])
      continue
    }

    flushList()

    const heading = /^(#{1,4})\s+(.*)$/.exec(line)
    if (heading) {
      blocks.push(
        <h4
          key={`h-${key++}`}
          className="mb-1.5 mt-4 font-display text-sm font-semibold text-paper first:mt-0"
        >
          {inline(heading[2], `h-${key}`)}
        </h4>,
      )
      continue
    }

    blocks.push(
      <p key={`p-${key++}`} className="my-2 leading-relaxed first:mt-0">
        {inline(line, `p-${key}`)}
      </p>,
    )
  }
  flushList()

  return <div className="text-tiny text-paper-dim">{blocks}</div>
}
