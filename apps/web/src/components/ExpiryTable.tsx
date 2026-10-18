import {
  dash,
  flagLabel,
  fmtAge,
  fmtDays,
  fmtGreek,
  fmtIv,
  fmtPrice,
  fmtUtc,
  hasSeriousFlag,
  nearestStrikeIndex,
} from '../lib/chain'
import type { Contract, Expiry } from '../lib/chain'
import type { ContractPick } from '../lib/pricing'

const SIDE_COLUMNS = [
  'Bid',
  'Ask',
  'Mark',
  'IV',
  'Deribit IV',
  'Delta',
  'Vega',
  'Flags',
]

function Cells({
  contract,
  onPrice,
}: {
  contract: Contract | null
  onPrice?: () => void
}) {
  if (contract === null) {
    return SIDE_COLUMNS.map((name) => (
      <td key={name} className="px-2 py-1 text-right text-slate-600">
        {dash}
      </td>
    ))
  }
  const serious = hasSeriousFlag(contract.flags)
  const flagText = contract.flags.map(flagLabel).join('\n')
  const quiet = serious ? 'text-amber-200' : 'text-slate-200'
  return (
    <>
      <td className={`px-2 py-1 text-right ${quiet}`}>
        {fmtPrice(contract.bid)}
      </td>
      <td className={`px-2 py-1 text-right ${quiet}`}>
        {fmtPrice(contract.ask)}
      </td>
      <td className={`px-2 py-1 text-right ${quiet}`}>
        {onPrice ? (
          <button
            type="button"
            onClick={onPrice}
            title="Open in the pricing calculator"
            className="underline decoration-dotted underline-offset-2 hover:text-sky-300"
          >
            {fmtPrice(contract.mark)}
          </button>
        ) : (
          fmtPrice(contract.mark)
        )}
      </td>
      <td className={`px-2 py-1 text-right ${quiet}`}>
        {fmtIv(contract.mark_iv)}
      </td>
      <td className="px-2 py-1 text-right text-slate-400">
        {fmtIv(contract.deribit_mark_iv)}
      </td>
      <td className="px-2 py-1 text-right text-slate-300">
        {fmtGreek(contract.delta, 3)}
      </td>
      <td className="px-2 py-1 text-right text-slate-300">
        {fmtGreek(contract.vega, 2)}
      </td>
      <td
        className={`px-2 py-1 text-center ${serious ? 'text-amber-300' : 'text-slate-500'}`}
        title={flagText || `quote ${fmtAge(contract.quote_age_seconds)} old`}
      >
        {contract.flags.length === 0 ? dash : contract.flags.length}
      </td>
    </>
  )
}

export function ExpiryTable({
  expiry,
  onPrice,
}: {
  expiry: Expiry
  onPrice?: (pick: ContractPick) => void
}) {
  const pickFor = (
    strike: number,
    optionType: 'call' | 'put',
    contract: Contract | null,
  ) =>
    onPrice && contract
      ? () =>
          onPrice({
            optionType,
            forward: expiry.forward,
            strike,
            yearsToExpiry: expiry.years_to_expiry,
            mark: contract.mark,
          })
      : undefined
  const atTheMoney = nearestStrikeIndex(expiry.strikes, expiry.forward)
  return (
    <section className="space-y-2">
      <h2 className="flex flex-wrap items-baseline gap-x-4 text-base font-medium text-slate-100">
        <span>{fmtUtc(expiry.expiration)}</span>
        <span className="text-sm font-normal text-slate-400">
          {fmtDays(expiry.years_to_expiry)} to expiry
        </span>
        <span className="text-sm font-normal text-slate-400">
          forward {fmtPrice(expiry.forward)} USDC
        </span>
      </h2>
      <div className="overflow-x-auto rounded border border-slate-800">
        <table className="w-full min-w-[64rem] border-collapse font-mono text-xs tabular-nums">
          <caption className="sr-only">
            Calls and puts by strike for expiry {fmtUtc(expiry.expiration)}. IV
            columns are in percent.
          </caption>
          <thead className="bg-slate-900 text-slate-400">
            <tr>
              <th
                colSpan={SIDE_COLUMNS.length}
                scope="colgroup"
                className="px-2 py-1 text-center"
              >
                Calls
              </th>
              <th rowSpan={2} scope="col" className="px-3 py-1 text-center">
                Strike
              </th>
              <th
                colSpan={SIDE_COLUMNS.length}
                scope="colgroup"
                className="px-2 py-1 text-center"
              >
                Puts
              </th>
            </tr>
            <tr>
              {[0, 1].flatMap((side) =>
                SIDE_COLUMNS.map((name) => (
                  <th
                    key={`${side}-${name}`}
                    scope="col"
                    className="px-2 py-1 text-right font-normal"
                  >
                    {name}
                  </th>
                )),
              )}
            </tr>
          </thead>
          <tbody>
            {expiry.strikes.map((row, index) => (
              <tr
                key={row.strike}
                className={`border-t border-slate-800/70 ${index === atTheMoney ? 'bg-slate-800/60' : ''}`}
              >
                <Cells
                  contract={row.call}
                  onPrice={pickFor(row.strike, 'call', row.call)}
                />
                <th
                  scope="row"
                  className={`px-3 py-1 text-center ${index === atTheMoney ? 'text-white' : 'text-slate-300'}`}
                >
                  {row.strike.toLocaleString('en-US')}
                </th>
                <Cells
                  contract={row.put}
                  onPrice={pickFor(row.strike, 'put', row.put)}
                />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
