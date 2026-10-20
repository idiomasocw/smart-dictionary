-- Owner reviews, for Supabase's SQL Editor (2026-10-20). Blocks 1 and 3 only read; block 2
-- changes entries, and is for after the deploy that exempts a headword from its own V10.

-- 1. Quarantined entries and why: the checks their latest submission failed.
select e.key, e.cefr, r.created_at,
       array(select distinct err->>'check' from jsonb_array_elements(r.qa->'errors') err)
         as failed_checks,
       array(select err->>'message' from jsonb_array_elements(r.qa->'errors') err limit 3)
         as first_problems
from entries e
join lateral (select qa, created_at from entry_revisions
              where entry_id = e.id order by created_at desc limit 1) r on true
where e.status = 'quarantined'
order by e.priority desc;

-- 2. Requeue the ones only V10 stopped (kill: its examples held the headword itself). Each
--    gets its attempts back; the next runs write it with the fixed check. The console's
--    Requeue button does the same for one entry at a time.
update entries e set status = 'queued', attempts = 0, updated_at = now()
where e.status = 'quarantined' and e.published_at is null
  and (select bool_and(err->>'check' = 'V10')
       from jsonb_array_elements((select qa->'errors' from entry_revisions
                                  where entry_id = e.id order by created_at desc limit 1)) err)
returning e.key;

-- 3. Seeded headwords that hold a word on the safety list. Each one is your choice (seeded),
--    so the routine now writes it, with calm, non-graphic examples. Retire any you don't want
--    for 16+ learners, in the console or with:
--      update entries set status = 'retired', updated_at = now() where key = '<key>';
select key, cefr, status::text
from entries
where status <> 'retired'
  and string_to_array(lemma_norm, ' ') && array['sex', 'sexy', 'sexual', 'porn', 'porno',
    'pornography', 'naked', 'nude', 'orgasm', 'rape', 'raped', 'rapist', 'murder', 'murdered',
    'murderer', 'kill', 'killed', 'killing', 'suicide', 'torture', 'terrorist', 'terrorism',
    'bomb', 'cocaine', 'heroin', 'meth', 'drunk', 'nazi', 'genocide']
order by cefr nulls last, key;
