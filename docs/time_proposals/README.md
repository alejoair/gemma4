# Time proposals from six independent agents (10-16)

The user asked for independent proposals to cut the system's time, each from a different technique, with only the
essential context. Each agent read only `brief.md` (constraints, how a task runs, V23's aggregate measurements; none of
our design documents or pending ideas). Only F also read the raw traces of V22 and V23, so that the other five
proposed from their technique and F from the evidence. None opened the repository.

| Agent | Angle | File |
|---|---|---|
| A | token economy and generation config | `A_tokens.md` |
| B | orchestration topology (ADK) | `B_topology.md` |
| C | deterministic offloading | `C_offloading.md` |
| D | anytime / budget-aware ordering | `D_anytime.md` |
| E | literature and public systems (cited) | `E_literature.md` |
| F | evidence from the traces | `F_traces.md` |

## Where they agree

| Proposal | Agents | Saving claimed per task | State |
|---|---|---|---|
| Copies of the same code: specify and edit one, copy the edit | A3, B5, C2, D4, E5 | 7-25 s on average, 50-100 s where copies exist | **done in V24** (identical original code); async/sync twins by a mechanical rewrite not done |
| Parallel specify (ParallelAgent, vLLM batches requests) | B1, D5, E1, F (bet) | 35-90 s | needs two checks first: vLLM throughput with 2-4 requests on 4×L4 (the harness does not set `max_num_seqs`, but it was never measured), and ParallelAgent as an agent_tool with one state file per slot (our scripts lock one state) |
| Coordinator without deliberation (minimal thinking, shorter return lines, fixed first call) | A2, B3A, C3B, E3, F4 | 12-35 s | not done |
| Scope folded into specify / decided after the first edit; auto-added "same pattern" places only suggestions | C1, F1, D1 | 30-70 s | not done |
| The main place first, the rest after (two waves, value order with the clock) | D1, D2, C1 | earlier first diff (about 138 s instead of 240 s) | not done |
| `missing` protocol: re-open a finished place, no chains, the place goes on in the same call | F2 | about 17 s | **mostly done in V24**; open: `missing` on the place itself counts as its sentence, a file path as its top level |
| Literal "replace `A` with `B`" sentences applied by the script (6 of 15 in V23) | F3 | about 13 s | not done |
| Requirements in one decision (the script extracts the names; or selection by number) | F5, C4, B4 | 8-20 s | not done |
| Run the issue's own example code as a zero-token reproduction | C5 | 10-20 s, mainly quality | not done |

## Where they disagree

- **A smaller specify budget** (A1: 1,024 → 640 → 512 with conclusion-first wording) against **keeping it** (F, from the
  traces: the answer appears between 30% and 85% of the way through the thinking, and in rich_3521 a "no change" cut by
  the budget was wrong). E2's middle way: the small budget only for derived places (copies, twins, callers).
- **Replacing the coordinator with SequentialAgent/LoopAgent** (B3B, E3, C3A) against **keeping it with less thinking**
  (F: shared history, no conditional routing, unknown `include_contents` semantics in loops; large effort).

## Rejected by most

Stop sequences (they can cut the tool-call markup), `include_thoughts: false`, penalties, a LoRA for shorter thinking
(large effort; allowed only with the model's own traces), several full attempts, speculative decoding (server flag).
