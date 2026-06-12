# Family Budget — Architecture

The Family Budget agent owns the money in the [Chief of Staff personal operating system](https://drsinabari.com/articles/chief-of-staff-personal-operating-system.html). The main article gives it one paragraph; this page is the long version: how it's wired, what the wallets actually are, what the waterfall actually does, what runs on a cron, what escalates to a human, and which earlier versions turned out to be wrong.

## 1. Wiring

Three hops, no MCP.

```
bank accounts + credit cards
        │
        ▼  (YNAB's native bank-import connections)
       YNAB
        │
        ▼  (REST API, token in local secrets file)
   Python helper in scripts/
        │
        ▼  (read-write on allocations only)
   Family Budget agent
```

There is no MCP server in this stack. Claude Cowork's sandbox cannot host local stdio MCP servers, so the integration is a small Python helper in the project's `scripts/` directory plus a token sitting in a local secrets file.

The agent is read-write but only on allocations. It assigns dollars to YNAB categories, never moves real money. Actual bank transfers stay flagged for human approval.

Opaque merchants get their own ingestion path. Costco and Amazon report line items only on order-confirmation pages, not in YNAB's transaction feed, so browser-automation crons scrape those order-history pages into weekly CSVs and drop them into the queue. The CSV reconciliation runs Fridays.

## 2. Wallet structure

Thirty-five categories in eight groups:

1. **Tax obligations** — federal and state estimated tax, property tax (monthly accrual against two annual installments)
2. **Debt service** — mortgage, student loan(s), revolving paydown envelope
3. **Cash buffer** — operating reserve with a target band
4. **Retirement** — periodic contributions, tracked but funded from the appropriate source
5. **Fixed recurring** — utilities, insurance, subscriptions, baseline bills
6. **Variable living** — groceries, gas, household, kids' activities, miscellaneous
7. **Discretionary** — dining, entertainment, gifts, personal
8. **Side-business catchall** — net-tracked; line items left to card statements at tax time

### Holdback wallets specifically

The holdback wallets are the heart of why this works. They reserve dollars before they get spent:

- **Federal estimated tax** — quarterly accrual
- **State estimated tax** — quarterly accrual
- **Property tax** — monthly accrual against two annual installments
- **Operating buffer** — target band rather than a single number; the waterfall behaves differently inside vs outside the band (see §3)
- **Debt-paydown envelope** — receives the residual after priority allocation, blended with the buffer
- **Staging envelope for Costco / Amazon charges** — holds the bulk charge until the line items arrive, then disburses to the right categories on Friday's scrape pass

### Growth profile

The count grows slowly: 34 → 35 this spring when a student loan exited forbearance. One deprecated envelope sits at `$0` rather than being deleted, preserving the historical row in YNAB reports.

## 3. The waterfall

Dynamic in the sense that fill status drives every event, but the ordering is strict.

### Lifecycle of one inflow

1. **Deposit lands** (paycheck, side-business income, transfer).
2. **Script runs in simulate mode** and proposes an allocation. Nothing executes yet.
3. **Operator confirms** (or rejects, or modifies).
4. **Allocation executes** against YNAB via the REST API.

The simulate-propose-confirm-execute loop is non-negotiable. Every income-event allocation gets human eyes before it lands.

### The fill formula

Fill status is a dollar gap, not a percentage:

```
gap   = target − budgeted_this_month
fund  = min(gap, pool)
pool -= fund
```

### Strict priority order

For each deposit, wallets are funded in this strict list order:

1. Tax obligations
2. Fixed obligations
3. Variable living
4. Discretionary

Next wallet in the list only sees a dollar after the current one is fully funded. "Equally underfilled" cannot arise — strict ordering means the priority list itself is the tiebreaker.

### Current month before next month

The current month fills completely before the next month gets a dollar. Once the current month is full at every priority tier, the system pre-funds the next month at the same priorities. This is what "always one month ahead" means in practice.

### Marginal residual split

What survives the priority pass splits between cash buffer and debt paydown, **marginally**, based on the running buffer balance:

| Buffer balance | Buffer share | Debt-paydown share |
|---|---|---|
| Below the target band | 60 | 0 |
| Inside the band | 30 | 30 |
| Above the band | 0 | 60 |

Rates blend as the running balance crosses a threshold mid-deposit, so paydown can begin partway through a single event. There is no hysteresis — the thresholds themselves are the mechanism. The residual after that stays unassigned, available for the next discretionary decision.

### Diagram

```
                Deposit lands
                       │
                       ▼
  ┌───────────────────────────────┐
  │ Priority allocation           │  strict order; for each:
  │  1. Tax obligations           │    gap = target − budgeted
  │  2. Fixed obligations         │    fund = min(gap, pool)
  │  3. Variable living           │  next wallet only after
  │  4. Discretionary             │  current is full
  └───────────────────────────────┘
                       │
                       ▼
  Current month full?  Yes → pre-fund NEXT month, same priorities
                       │
                       ▼
  ┌───────────────────────────────┐
  │ Marginal residual split       │  buffer / debt blend, by
  │  buffer below band:  60 / 0   │  buffer balance; rates
  │  buffer inside band: 30 / 30  │  blend across thresholds
  │  buffer above band:   0 / 60  │  within a single event
  └───────────────────────────────┘
                       │
                       ▼
                Unassigned remainder
```

### Policy as a versioned spec

The waterfall policy is a versioned markdown spec, currently at **v1.7**. The version number is load-bearing: it lets me explain to myself what changed and when, and it makes the "what I got wrong" log below auditable rather than mythological. See §6.

## 4. What runs on a cron, unprompted

| Cadence | Job | What it does |
|---|---|---|
| Daily | Backups | Snapshot the YNAB state and the project's working files. |
| Daily | Task sync | Parses the project's task file into external tasks and calendar events for the executive layer. |
| Friday | Costco scrape | Browser-automation pull of recent order-history line items into CSV. |
| Friday | Amazon scrape | Same, for Amazon. |
| Friday | Categorization proposal | Generates exactly one review task: "approve the proposed categorizations for this week's scrapes." |
| 1st of month | Funding script | Tops up the current month, then pre-funds the next, idempotent. A missed run on the 1st can be safely re-run later. |

Weekly jobs run **Friday by instruction, never Sunday**. The instruction is explicit because Sunday runs surfaced things at the wrong time of week — review work bled into family time.

## 5. What escalates

The agent is conservative about flagging items for human review. The list:

- **Anything moving real money.** The agent never executes a bank transfer. It writes a proposal.
- **Every income-event allocation.** Proposed, never auto-executed.
- **The weekly review task.** Single line item: approve the week's scraped categorizations.
- **Anomalies.** It once caught a paycheck that came in with zero federal withholding before I noticed.
- **Dated deadlines.** Appended to the project's task file so the executive layer syncs them outward into Google Calendar and Google Tasks.

## 6. What earlier versions got wrong

The waterfall policy is at v1.7. Here is what changed and why:

### v1.0–1.2 (early): the bills never got funded

The early versions jumped from the tax reserve straight to building the cash buffer. The month's actual bills never got funded; envelopes sat at `$0` until I caught it manually. **Fixed 2026-05-20** by inserting the explicit fixed-obligations tier between tax and buffer.

### Retirement accrual was a phantom reserve

A retirement accrual line existed in the priority list, but the contribution legally had to come from business funds, not household income. The accrual was double-counting against the wrong account. **Removed 2026-05-02.**

### Buffer band double-counted a month of cushion

Once the system pre-funded one month ahead, the buffer band's target was effectively asking for a second month of cushion on top. **Lowered 2026-05-22** to reflect that pre-funding already provides one of the months.

### Per-source tax skim was over-precision

Early versions tracked a per-source tax skim (rate adjusted per income type). It was precision the household didn't actually need once safe-harbor was met. **Replaced 2026-05-29** with flat monthly tax targets. Validated days later when exactly the zero-withholding paystub showed up — the flat target absorbed it without recalculating; the per-source version would have under-reserved that paycheck and over-corrected the next one.

### Buffer-versus-debt split was bracketed, not marginal

Originally applied one flat bracket to a whole deposit, so a deposit that crossed a threshold got allocated entirely under the old bracket — delaying paydown on exactly the event that triggered it. **Replaced 2026-05-29** with the marginal blend described in §3. Now paydown starts partway through the deposit that crosses the band.

## Related

- Main article: [The Chief of Staff: Building a Local Voice Agent as a Personal Operating System](https://drsinabari.com/articles/chief-of-staff-personal-operating-system.html)
- Author: [Dr. Sina Bari, MD](https://sinabarimd.com/about)
