// Procgen Studio pane: recent renders, their source and seed, and a local re-roll button.
// Talks only to this plugin's own backend (dashboard/plugin_api.py) through ctx.rest.
import { Button, PANES_AREA, PALETTE_AREA, host, useQuery, useQueryClient } from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const muted = 'text-(--ui-text-tertiary)'
const faint = 'text-(--ui-text-quaternary)'
const border = 'border border-(--ui-stroke-secondary)'

function Thumb({ item, active, onSelect }) {
  return jsxs('button', {
    type: 'button',
    title: item.prompt,
    onClick: () => onSelect(item.id),
    className: `relative block aspect-square w-full overflow-hidden rounded-[3px] ${border} ${
      active ? 'ring-2 ring-(--ui-accent)' : ''
    }`,
    children: [
      item.thumb
        ? jsx('img', { src: item.thumb, alt: item.prompt, className: 'h-full w-full object-cover' })
        : jsx('div', { className: `flex h-full items-center justify-center text-[0.625rem] ${faint}`, children: item.media }),
      item.media === 'video'
        ? jsx('span', { className: 'absolute right-1 bottom-1 rounded-[2px] bg-black/60 px-1 text-[0.5625rem] text-white', children: 'MP4' })
        : null
    ]
  })
}

function Row({ label, value }) {
  return jsxs('div', {
    className: 'flex gap-2 text-[0.6875rem] leading-4',
    children: [
      jsx('span', { className: `w-14 shrink-0 ${faint}`, children: label }),
      jsx('span', { className: 'min-w-0 break-words text-(--ui-text-secondary)', children: value })
    ]
  })
}

function Detail({ ctx, id, onRerolled }) {
  const detail = useQuery({ queryKey: [ctx.source, 'render', id], queryFn: () => ctx.rest(`/renders/${id}`) })
  const [seed, setSeed] = useState('')
  const [busy, setBusy] = useState(false)
  const [showSource, setShowSource] = useState(false)

  useEffect(() => setSeed(''), [id])

  if (detail.isLoading) return jsx('div', { className: `p-3 text-xs ${muted}`, children: 'Loading…' })
  if (detail.error || !detail.data) {
    return jsx('div', { className: `p-3 text-xs ${muted}`, children: `Could not load ${id}: ${String(detail.error || '')}` })
  }
  const d = detail.data

  const reroll = async random => {
    setBusy(true)
    try {
      const body = random || seed.trim() === '' ? {} : { seed: Number(seed) }
      const next = await ctx.rest(`/renders/${id}/reroll`, { method: 'POST', body, timeoutMs: 180000 })
      host.notify({ kind: 'success', message: `Re-rolled ${id} → ${next.id} (seed ${next.seed}), no model call` })
      onRerolled(next.id)
    } catch (err) {
      host.notifyError ? host.notifyError(err) : host.notify({ kind: 'error', message: String(err) })
    } finally {
      setBusy(false)
    }
  }

  return jsxs('div', {
    className: 'flex flex-col gap-2 p-3',
    children: [
      d.image ? jsx('img', { src: d.image, alt: d.prompt, className: `w-full rounded-[3px] ${border}` }) : null,
      jsx('div', { className: 'text-xs leading-4 text-(--ui-text-secondary)', children: d.prompt }),
      jsx(Row, { label: 'id', value: `${d.id}${d.parent ? `  ← ${d.parent}` : ''}` }),
      jsx(Row, { label: 'seed', value: String(d.seed) }),
      jsx(Row, { label: 'style', value: `${d.style} / ${d.palette}` }),
      jsx(Row, { label: 'source', value: `${d.kind}  ${d.size[0]}×${d.size[1]}` }),
      d.score !== null && d.score !== undefined
        ? jsx(Row, { label: 'critic', value: `${d.score}/10 after ${d.attempts} attempt(s)` })
        : null,
      d.set ? jsx(Row, { label: 'set', value: d.set }) : null,
      jsxs('div', {
        className: 'mt-1 flex items-center gap-1.5',
        children: [
          jsx('input', {
            value: seed,
            onChange: e => setSeed(e.target.value.replace(/[^0-9]/g, '')),
            placeholder: 'seed',
            inputMode: 'numeric',
            className: `w-20 rounded-[2.5px] bg-transparent px-1.5 py-1 text-xs ${border}`
          }),
          jsx(Button, { size: 'sm', disabled: busy, onClick: () => reroll(false), children: busy ? 'Rendering…' : 'Re-roll' }),
          jsx(Button, { size: 'sm', variant: 'ghost', disabled: busy, onClick: () => reroll(true), children: 'Random' })
        ]
      }),
      jsxs('div', {
        className: 'flex flex-wrap gap-1.5',
        children: [
          jsx(Button, {
            size: 'xs',
            variant: 'ghost',
            onClick: async () => {
              const ok = await ctx.os.writeClipboard(d.paths.source)
              host.notify({ kind: ok ? 'success' : 'error', message: ok ? 'Source path copied' : 'Clipboard unavailable' })
            },
            children: 'Copy source path'
          }),
          jsx(Button, { size: 'xs', variant: 'ghost', onClick: () => ctx.os.revealPath(d.paths.output), children: 'Reveal file' }),
          jsx(Button, { size: 'xs', variant: 'ghost', onClick: () => setShowSource(!showSource), children: showSource ? 'Hide source' : 'Show source' })
        ]
      }),
      showSource
        ? jsx('pre', {
            className: `max-h-80 overflow-auto rounded-[3px] p-2 font-mono text-[0.625rem] leading-4 whitespace-pre ${border} text-(--ui-text-secondary)`,
            children: d.source
          })
        : null
    ]
  })
}

function ProcgenPane({ ctx }) {
  const qc = useQueryClient()
  const [selected, setSelected] = useState(null)
  const list = useQuery({
    queryKey: [ctx.source, 'renders'],
    queryFn: () => ctx.rest('/renders?limit=24'),
    refetchInterval: 5000
  })
  const renders = (list.data && list.data.renders) || []
  const current = selected || (renders[0] && renders[0].id)

  const onRerolled = nextId => {
    qc.invalidateQueries({ queryKey: [ctx.source, 'renders'] })
    setSelected(nextId)
  }

  if (list.error) {
    return jsx('div', {
      className: `p-3 text-xs ${muted}`,
      children: 'Procgen Studio backend is not reachable. Enable the plugin (hermes plugins enable procgen-studio) and restart the gateway.'
    })
  }
  if (!list.isLoading && renders.length === 0) {
    return jsx('div', {
      className: `p-3 text-xs leading-5 ${muted}`,
      children: 'No renders yet. Set image_gen.provider to procgen-studio and ask Hermes for an icon, logo or scene.'
    })
  }
  return jsxs('div', {
    className: 'flex h-full flex-col overflow-y-auto',
    children: [
      jsx('div', {
        className: 'grid grid-cols-4 gap-1.5 p-3 pb-0',
        children: renders.map(item => jsx(Thumb, { key: item.id, item, active: item.id === current, onSelect: setSelected }))
      }),
      current ? jsx(Detail, { ctx, id: current, onRerolled }) : null
    ]
  })
}

export default {
  id: 'procgen-studio',
  name: 'Procgen Studio',
  register(ctx) {
    ctx.register({
      id: 'pane',
      area: PANES_AREA,
      title: 'procgen',
      data: { placement: 'right', width: '340px' },
      render: () => jsx(ProcgenPane, { ctx })
    })
    ctx.register({
      id: 'reroll-latest',
      area: PALETTE_AREA,
      data: {
        id: 'procgen-studio.reroll-latest',
        label: 'Procgen Studio: re-roll the latest render',
        keywords: ['procgen', 'seed', 'reroll', 'image'],
        run: async () => {
          try {
            const list = await ctx.rest('/renders?limit=1')
            const latest = list.renders && list.renders[0]
            if (!latest) {
              host.notify({ kind: 'info', message: 'No Procgen Studio renders yet' })
              return
            }
            const next = await ctx.rest(`/renders/${latest.id}/reroll`, { method: 'POST', body: {}, timeoutMs: 180000 })
            host.notify({ kind: 'success', message: `Re-rolled ${latest.id} → ${next.id} (seed ${next.seed})` })
          } catch (err) {
            host.notify({ kind: 'error', message: `Re-roll failed: ${String(err)}` })
          }
        }
      }
    })
  }
}
