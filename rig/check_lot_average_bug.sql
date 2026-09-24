-- Diagnostic: does the "Dépistage visuel par lot" average read low because it's
-- averaging BOTH rows of the two-prompt pair instead of just the deciding row?
--
-- Under the two-prompt rule (app/main.py combined_instruction / operator_prompt_pair),
-- every operator sample writes TWO rows to vision_band_estimates -- one from the
-- "high" prompt, one from the "low" prompt -- but only one of them is the decider
-- (operator_instruction IS NOT NULL on that row; the other row of the pair has
-- operator_instruction IS NULL). The low prompt deliberately under-reads anything
-- above ~8%, so averaging in its row for samples the high prompt actually decided
-- will drag a lot's average down.
--
-- Usage: set :lot to the lot_number_text you're checking, or drop the WHERE clause
-- to run it across every lot at once (second query).

-- 1) Per-lot comparison: naive average (every row) vs correct average (decider only)
select
    lot_number_text,
    count(*)                                                   as rows_total,
    count(*) filter (where operator_instruction is not null)   as rows_decider,
    round(avg(estimate_pct)::numeric, 2)                       as avg_naive_all_rows,
    round(avg(estimate_pct) filter (where operator_instruction is not null)::numeric, 2)
                                                                as avg_decider_only,
    round(
        (avg(estimate_pct) filter (where operator_instruction is not null)
         - avg(estimate_pct))::numeric, 2
    )                                                           as delta
from vision_band_estimates
where source = 'operator'
  and is_training = false
group by lot_number_text
order by lot_number_text desc
limit 50;

-- 2) Row-level detail for ONE lot, to see the two-prompt pairing directly.
-- Replace 'YOUR-LOT-NUMBER' below.
select
    id,
    created_at,
    prompt_version,
    predicted_band,
    estimate_pct,
    operator_instruction,      -- non-null = this row decided the instruction shown
    escalated
from vision_band_estimates
where lot_number_text = 'YOUR-LOT-NUMBER'
  and source = 'operator'
order by created_at;
